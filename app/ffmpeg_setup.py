"""Make sure an `ffmpeg` program is available.

ffmpeg is a separate program, not a Python package, so `pip install` can't put
it on the system. If it isn't installed, we use the static ffmpeg build that
ships inside the `imageio-ffmpeg` pip package: it's copied once to
~/.cache/instasave/bin/ffmpeg(.exe) and that folder is added to PATH, so both
our code and yt-dlp find it under its normal name.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
from pathlib import Path

log = logging.getLogger("uvicorn.error")

BIN_DIR = Path(os.environ.get("INSTASAVE_BIN_DIR", Path.home() / ".cache" / "instasave" / "bin"))


def is_termux() -> bool:
    return "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", "")


def ensure_ffmpeg() -> str | None:
    """Return the path of a usable ffmpeg, installing the bundled one if needed."""
    if found := shutil.which("ffmpeg"):
        return found
    if is_termux():  # the pip build is for regular Linux and won't run on Android
        log.warning("ffmpeg not found. In Termux run: pkg install ffmpeg")
        return None
    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:  # package missing or no binary for this platform
        log.warning("ffmpeg not found and the bundled copy is unavailable (%s). "
                    "Install ffmpeg: https://ffmpeg.org/download.html", exc)
        return None

    target = BIN_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    try:
        if not target.exists() or target.stat().st_size != bundled.stat().st_size:
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            shutil.copy2(bundled, tmp)
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(tmp, target)
        os.environ["PATH"] = str(BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
    except OSError as exc:
        log.warning("Could not set up the bundled ffmpeg: %s", exc)
        return None
    return shutil.which("ffmpeg")
