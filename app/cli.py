"""Command line usage:
  python -m app.cli <instagram-or-youtube-link> [--audio] [--quality 720] [-o DIR]"""

import argparse
import os
import re
import shutil
import subprocess
import sys

import httpx

from . import youtube
from .ffmpeg_setup import ensure_ffmpeg
from .extractor import ExtractionError, resolve

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Download Instagram / YouTube videos, images, stories or audio.")
    p.add_argument("url", help="Instagram post / reel / story link, or YouTube video link")
    p.add_argument("--audio", action="store_true", help="save the audio of videos as MP3")
    p.add_argument("--quality", type=int, help="YouTube: max video height, e.g. 1080 or 720 (default: best)")
    p.add_argument("-o", "--output", default="downloads", help="output folder (default: downloads)")
    args = p.parse_args(argv)
    ensure_ffmpeg()

    try:
        result = resolve(args.url)
    except ExtractionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)
    if result.platform == "youtube":
        return _youtube(result, args)
    base = re.sub(r"[^\w.-]+", "_", "_".join(x for x in (result.owner, result.shortcode or result.kind) if x))
    for item in result.items:
        suffix = f"_{item.index + 1}" if len(result.items) > 1 else ""
        if args.audio and item.type == "video":
            if not shutil.which("ffmpeg"):
                print("Error: ffmpeg is required for --audio", file=sys.stderr)
                return 1
            path = os.path.join(args.output, f"{base}{suffix}.mp3")
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-user_agent", UA,
                            "-i", item.url, "-vn", "-acodec", "libmp3lame", "-q:a", "2", path], check=True)
        else:
            path = os.path.join(args.output, f"{base}{suffix}.{item.ext}")
            with httpx.stream("GET", item.url, headers={"User-Agent": UA},
                              follow_redirects=True, timeout=60) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        print(f"Saved {path}")
    return 0


def _youtube(result, args) -> int:
    import tempfile

    mode = "audio" if args.audio else "video"

    def progress(frac, stage):
        print(f"\r{stage}: {frac:6.1%}   ", end="", flush=True)

    with tempfile.TemporaryDirectory(dir=args.output) as tmp:
        try:
            path = youtube.download(result.source_url, mode, tmp, args.quality, progress)
        except ExtractionError as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            return 1
        name = re.sub(r'[\\/:*?"<>|]+', "", result.title or result.shortcode).strip()[:100]
        dest = os.path.join(args.output, f"{name}{os.path.splitext(path)[1]}")
        shutil.move(path, dest)
    print(f"\nSaved {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
