"""Keep the extractor libraries up to date automatically.

Instagram and YouTube change often, and yt-dlp / instaloader ship fixes quickly.
This module checks PyPI for newer releases (at startup, every
AUTO_UPDATE_INTERVAL_HOURS, and soon after a failed download), installs them
with pip, then restarts the server process once no requests are in flight so
the new code is loaded.

Settings (environment variables):
  AUTO_UPDATE=0                     disable the updater
  AUTO_UPDATE_INTERVAL_HOURS=6      how often to check
  AUTO_UPDATE_RESTART=0             install updates but don't restart (apply on next start)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from importlib.metadata import PackageNotFoundError, version as installed_version

import httpx
from packaging.version import InvalidVersion, Version

log = logging.getLogger("uvicorn.error")  # shows up in the server log

PACKAGES = ("yt-dlp", "instaloader", "bgutil-ytdlp-pot-provider")
# extras to keep installed alongside a package (yt-dlp[default] pulls in yt-dlp-ejs for YouTube)
INSTALL_SPEC = {"yt-dlp": "yt-dlp[default]"}
ENABLED = os.environ.get("AUTO_UPDATE", "1") != "0"
INTERVAL = float(os.environ.get("AUTO_UPDATE_INTERVAL_HOURS", "6")) * 3600
RESTART = os.environ.get("AUTO_UPDATE_RESTART", "1") != "0"
FAILURE_CHECK_COOLDOWN = 3600  # after a failed download, check at most once an hour
RESTART_WAIT_LIMIT = 900       # max seconds to wait for in-flight downloads before restarting

state = {
    "enabled": ENABLED,
    "last_check": None,
    "last_result": None,
    "restart_pending": False,
}
_lock = threading.Lock()
_wake = threading.Event()
_active_requests = 0
_active_lock = threading.Lock()
_busy_checks: list = []  # callables returning how many background jobs are running


def register_busy(check) -> None:
    if check not in _busy_checks:
        _busy_checks.append(check)


def _busy() -> bool:
    return _active_requests > 0 or any(check() for check in _busy_checks)


# --------------------------------------------------------------------------- #
# request tracking (so restarts don't cut off downloads)
# --------------------------------------------------------------------------- #

def request_started() -> None:
    global _active_requests
    with _active_lock:
        _active_requests += 1


def request_finished() -> None:
    global _active_requests
    with _active_lock:
        _active_requests -= 1


# --------------------------------------------------------------------------- #
# version checks
# --------------------------------------------------------------------------- #

def current_versions() -> dict[str, str | None]:
    out = {}
    for pkg in PACKAGES:
        try:
            out[pkg] = installed_version(pkg)
        except PackageNotFoundError:
            out[pkg] = None
    return out


def latest_version(pkg: str) -> str:
    r = httpx.get(f"https://pypi.org/pypi/{pkg}/json", timeout=20)
    r.raise_for_status()
    return r.json()["info"]["version"]


def outdated() -> dict[str, tuple[str | None, str]]:
    """Return {package: (installed, latest)} for packages with a newer release."""
    result = {}
    for pkg, cur in current_versions().items():
        latest = latest_version(pkg)
        try:
            newer = cur is None or Version(latest) > Version(cur)
        except InvalidVersion:
            newer = latest != cur
        if newer:
            result[pkg] = (cur, latest)
    return result


def install(pkgs: dict[str, tuple[str | None, str]]) -> None:
    specs = [f"{INSTALL_SPEC.get(p, p)}=={latest}" for p, (_, latest) in pkgs.items()]
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", *specs],
        check=True, timeout=600, capture_output=True,
    )


def check_and_update() -> dict:
    """Check for updates and install them. Returns a summary dict."""
    with _lock:
        state["last_check"] = time.time()
        try:
            pending = outdated()
            if not pending:
                state["last_result"] = "up to date"
                return {"updated": {}}
            log.info("Updating: %s", ", ".join(f"{p} {a} -> {b}" for p, (a, b) in pending.items()))
            install(pending)
            state["last_result"] = "updated " + ", ".join(f"{p} {b}" for p, (_, b) in pending.items())
            if RESTART:
                state["restart_pending"] = True
            return {"updated": {p: b for p, (_, b) in pending.items()}}
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or b"").decode(errors="ignore").strip().splitlines()[-1:] or [str(exc)]
            state["last_result"] = f"pip failed: {err[0]}"
        except Exception as exc:  # network errors etc. — just try again later
            state["last_result"] = f"check failed: {exc}"
        log.warning("Auto-update: %s", state["last_result"])
        return {"error": state["last_result"]}


def notify_failure() -> None:
    """Called when a download fails; may be caused by an outdated extractor."""
    if not ENABLED:
        return
    last = state["last_check"] or 0
    if time.time() - last > FAILURE_CHECK_COOLDOWN:
        _wake.set()


# --------------------------------------------------------------------------- #
# background loop + restart
# --------------------------------------------------------------------------- #

def restart_process() -> None:
    """Replace the current process with a fresh one running the same command."""
    deadline = time.time() + RESTART_WAIT_LIMIT
    while _busy() and time.time() < deadline:
        time.sleep(1)
    log.info("Restarting to load updated packages")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *_original_argv()])


def _original_argv() -> list[str]:
    # `python -m app` -> sys.argv[0] is the path of app/__main__.py; restart as a module.
    if sys.argv and sys.argv[0].endswith(os.path.join("app", "__main__.py")):
        return ["-m", "app", *sys.argv[1:]]
    return sys.argv  # e.g. the `uvicorn` console script, which is itself a Python file


def _loop() -> None:
    while True:
        result = check_and_update()
        if result.get("updated") and RESTART:
            restart_process()
        _wake.wait(INTERVAL)
        _wake.clear()


def start() -> None:
    if not ENABLED:
        log.info("Auto-update disabled")
        return
    threading.Thread(target=_loop, name="auto-updater", daemon=True).start()


def status() -> dict:
    return {**state, "versions": current_versions(), "interval_hours": INTERVAL / 3600}
