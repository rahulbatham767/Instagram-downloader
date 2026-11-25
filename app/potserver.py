"""Run the YouTube PO token server automatically when not using Docker.

The bgutil-ytdlp-pot-provider plugin (from requirements.txt) needs its token
server running. Docker images start it themselves; for a plain
`pip install -r requirements.txt` + `python -m app` setup, this module:

  1. downloads the server source matching the installed plugin version
     (once, ~/.cache/instasave/bgutil/<version>/),
  2. installs its dependencies with Deno (from requirements.txt), or with
     Node.js/npm where Deno can't run (Termux on Android),
  3. starts it on 127.0.0.1:4416 in the background and restarts it if it exits.

It runs in a background thread, so the app starts immediately; YouTube
protection becomes active when setup finishes (about a minute the first time).

AUTO_POT_SERVER=0 turns this off (the Docker image does, as it runs its own).
"""

from __future__ import annotations

import atexit
import io
import logging
import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import youtube

log = logging.getLogger("uvicorn.error")

ENABLED = os.environ.get("AUTO_POT_SERVER", "1") != "0"
CACHE = Path(os.environ.get("INSTASAVE_CACHE_DIR", Path.home() / ".cache" / "instasave")) / "bgutil"
REPO = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider"
TARBALL = os.environ.get("BGUTIL_TARBALL_URL", REPO + "/archive/refs/tags/{version}.tar.gz")

state = {"status": "disabled" if not ENABLED else "not started", "error": None}


def _set(status: str, error: str | None = None) -> None:
    state["status"], state["error"] = status, error
    if error:
        log.warning("YouTube PO token server: %s (%s)", status, error)
    else:
        log.info("YouTube PO token server: %s", status)


def plugin_version() -> str | None:
    try:
        return pkg_version("bgutil-ytdlp-pot-provider")
    except PackageNotFoundError:
        return None


def _local_port() -> int | None:
    """Only manage a server for a local URL like http://127.0.0.1:4416."""
    u = urlparse(youtube.POT_PROVIDER_URL)
    if u.hostname in ("127.0.0.1", "localhost", "::1"):
        return u.port or 80
    return None


def _download_source(ver: str, dest: Path) -> None:
    """Fetch the server source for `ver` into dest/ (tarball, falling back to git)."""
    tmp = Path(tempfile.mkdtemp(prefix="bgutil_", dir=dest.parent))
    try:
        try:
            r = httpx.get(TARBALL.format(version=ver), follow_redirects=True, timeout=120)
            r.raise_for_status()
            with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(tmp, filter="data")  # blocks path traversal / links outside
                else:  # very old Python patch releases
                    base = tmp.resolve()
                    for m in tar.getmembers():
                        if not (base / m.name).resolve().is_relative_to(base) or m.issym() or m.islnk():
                            raise RuntimeError(f"unsafe path in archive: {m.name}")
                    tar.extractall(tmp)
            (root,) = [p for p in tmp.iterdir() if p.is_dir()]
        except Exception as exc:
            if not shutil.which("git"):
                raise RuntimeError(f"download failed: {exc}") from exc
            root = tmp / "src"
            subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", ver, REPO + ".git", str(root)],
                           check=True, timeout=300, capture_output=True)
        os.replace(root, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def runtime() -> str:
    """Deno where available (desktop, via pip); Node.js otherwise (e.g. Termux on Android)."""
    if shutil.which("deno"):
        return "deno"
    if shutil.which("node") and shutil.which("npm"):
        return "node"
    raise RuntimeError("needs Deno (pip install -r requirements.txt) or Node.js (Termux: pkg install nodejs-lts)")


def _sub(cmd: list[str], cwd: Path, timeout: int) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "DENO_NO_UPDATE_CHECK": "1"})
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines() or ["?"]
        raise RuntimeError(f"{Path(cmd[0]).name} {cmd[1]} failed: {lines[-1]}")


def ensure_installed(ver: str, rt: str) -> Path:
    """Return the server/ folder for `ver`, downloading and installing it if needed."""
    home = CACHE / f"{ver}-{rt}"
    server = home / "server"
    if (home / ".ready").exists():
        return server
    CACHE.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(home, ignore_errors=True)
    _set("downloading")
    _download_source(ver, home)
    _set("installing")
    if rt == "deno":
        _sub([shutil.which("deno"), "install", "--allow-scripts=npm:canvas", "--frozen"], server, 900)
    else:
        # --ignore-scripts skips building the optional `canvas` native addon, which
        # can't be built on Android; the server works without it.
        npm = shutil.which("npm")
        _sub([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], server, 1800)
        _sub([npm, "exec", "--offline", "--", "tsc"], server, 900)
    (home / ".ready").touch()
    return server


def server_command(server: Path, rt: str, port: int) -> list[str]:
    args = ["--host", "127.0.0.1", "--port", str(port)]
    if rt == "deno":
        nm = str(server / "node_modules")
        return [shutil.which("deno"), "run", "--allow-env", "--allow-net", f"--allow-ffi={nm}",
                f"--allow-read={nm}", "src/main.ts", *args]
    return [shutil.which("node"), "build/main.js", *args]


def _pidfile() -> Path:
    return CACHE / "server.pid"


def _stop_previous() -> None:
    """Stop a server we started earlier (e.g. before an auto-update restart)."""
    try:
        pid = int(_pidfile().read_text())
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
    except (OSError, ValueError):
        pass


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()


def _run(server: Path, rt: str, port: int) -> None:
    """Start the server and keep it running."""
    cmd = server_command(server, rt, port)
    logfile = CACHE / "server.log"
    failures = 0
    while failures < 5:
        with open(logfile, "wb") as out:  # a file, not a pipe: a full pipe would freeze the server
            proc = subprocess.Popen(cmd, cwd=server, stdout=out, stderr=subprocess.STDOUT,
                                    env={**os.environ, "DENO_NO_UPDATE_CHECK": "1"})
        _pidfile().write_text(str(proc.pid))
        atexit.register(_terminate, proc)
        for _ in range(60):  # wait for it to answer /ping
            if youtube.pot_provider_status()["reachable"] or proc.poll() is not None:
                break
            time.sleep(1)
        if proc.poll() is None:
            _set("running")
            failures = 0
            proc.wait()
        err = logfile.read_text(errors="ignore").strip().splitlines() if logfile.exists() else []
        failures += 1
        _set("restarting", err[-1] if err else f"exited with code {proc.returncode}")
        time.sleep(5)
    _set("failed", state["error"])


def _main() -> None:
    try:
        port = _local_port()
        ver = plugin_version()
        if port is None or ver is None:
            _set("not managed" if ver else "plugin not installed")
            return
        status = youtube.pot_provider_status()
        if status["reachable"] and status["version"] == ver:
            _set("running")  # already running (Docker, or started by an earlier run)
            return
        if status["reachable"]:
            _stop_previous()  # an older version we started before the plugin was updated
        rt = runtime()
        _run(ensure_installed(ver, rt), rt, port)
    except Exception as exc:
        _set("failed", str(exc))


def start() -> None:
    if ENABLED:
        threading.Thread(target=_main, name="pot-server", daemon=True).start()
