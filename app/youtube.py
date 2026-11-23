"""YouTube support (videos, Shorts) via yt-dlp.

YouTube serves high-quality video and audio as separate streams, so unlike
Instagram we can't just proxy one URL: the server downloads the streams,
merges them with ffmpeg (or converts to MP3), and then serves the file.

yt-dlp needs a JavaScript runtime (Deno) plus the yt-dlp-ejs package to get
YouTube formats; both are in requirements.txt.

Avoiding "Sign in to confirm you're not a bot" (see README for details):
  1. PO tokens   - the bgutil-ytdlp-pot-provider plugin (installed from requirements.txt)
                   asks a small provider server (docker-compose.yml runs one) for
                   Proof-of-Origin tokens, as recommended by the yt-dlp wiki.
  2. Cookies     - from a throwaway account; needed when the server's IP itself is flagged.
  3. Proxy       - route YouTube traffic through a residential / non-datacenter IP.
  4. Fallbacks   - on a bot check / 403 the request is retried with other YouTube clients.

Settings (environment variables):
  YT_COOKIES_FILE           cookies for YouTube (default: same file as IG_COOKIES_FILE)
  YT_POT_PROVIDER_URL       bgutil PO token server (default http://127.0.0.1:4416)
  YT_PROXY                  proxy for YouTube, e.g. socks5://127.0.0.1:1080 or http://user:pass@host:port
  YT_PLAYER_CLIENTS         comma-separated yt-dlp player clients (default: yt-dlp's defaults + mweb)
  YT_MAX_DURATION_MINUTES   refuse longer videos (default 180, 0 = no limit)
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .extractor import COOKIES_FILE, ExtractionError, MediaItem, MediaResult

YT_COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", COOKIES_FILE)
MAX_DURATION = int(os.environ.get("YT_MAX_DURATION_MINUTES", "180")) * 60
POT_PROVIDER_URL = os.environ.get("YT_POT_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
PROXY = os.environ.get("YT_PROXY", "").strip()
# The yt-dlp PO Token Guide recommends the mweb client together with a PO token provider.
PLAYER_CLIENTS = [c.strip() for c in os.environ.get("YT_PLAYER_CLIENTS", "default,mweb").split(",") if c.strip()]
# Tried in order when YouTube answers with a bot check / 403. These clients use different
# YouTube APIs; android_vr and web_embedded don't need PO tokens at all.
FALLBACK_CLIENTS = [["tv", "web_safari", "mweb"], ["android_vr", "web_embedded"]]
RETRYABLE = ("not a bot", "sign in to confirm", "http error 403", "forbidden", "po token",
             "requested format is not available", "only images are available")

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
            "youtu.be", "www.youtube-nocookie.com", "youtube-nocookie.com"}
VIDEO_ID = re.compile(r"^[\w-]{11}$")
PATH_ID = re.compile(r"^/(?:shorts|live|embed|v|e)/([\w-]{11})")
STANDARD_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)

ProgressFn = Callable[[float, str], None]  # (fraction 0..1, stage text)


def _parse(raw: str):
    raw = (raw or "").strip()
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    return urlparse(raw)


def is_youtube(raw: str) -> bool:
    return (_parse(raw).hostname or "").lower() in YT_HOSTS


def video_id(raw: str) -> str:
    u = _parse(raw)
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif m := PATH_ID.match(u.path):
        vid = m[1]
    else:
        vid = (parse_qs(u.query).get("v") or [""])[0]
    if not VIDEO_ID.match(vid or ""):
        raise ExtractionError(
            "Unsupported YouTube link. Use a video link like youtube.com/watch?v=... or youtu.be/..."
        )
    return vid


def watch_url(raw: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id(raw)}"


def _base_opts(clients: list[str] | None = None) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True,
        "extractor_args": {
            "youtube": {"player_client": clients or PLAYER_CLIENTS},
            "youtubepot-bgutilhttp": {"base_url": [POT_PROVIDER_URL]},
        },
        # a short pause between API requests helps stay under YouTube's rate limits
        "sleep_interval_requests": 1,
    }
    if os.path.isfile(YT_COOKIES_FILE):
        opts["cookiefile"] = YT_COOKIES_FILE
    if PROXY:
        opts["proxy"] = PROXY
    return opts


def _with_fallbacks(fn):
    """Run fn(clients); on a bot check / 403, retry with other YouTube clients."""
    attempts = [None, *FALLBACK_CLIENTS]
    for i, clients in enumerate(attempts):
        try:
            return fn(clients)
        except Exception as exc:
            if i == len(attempts) - 1 or not any(s in str(exc).lower() for s in RETRYABLE):
                raise


def pot_plugin_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("yt_dlp_plugins.extractor.getpot_bgutil_http") is not None


def pot_provider_status() -> dict:
    """Is the PO token provider server reachable? (it answers GET /ping with its version)"""
    import httpx

    try:
        r = httpx.get(f"{POT_PROVIDER_URL}/ping", timeout=2, trust_env=False)
        r.raise_for_status()
        return {"reachable": True, "version": r.json().get("version")}
    except Exception:
        return {"reachable": False, "version": None}


def bot_protection_status() -> dict:
    return {
        "pot_plugin": pot_plugin_installed(),
        "pot_provider": {"url": POT_PROVIDER_URL, **pot_provider_status()},
        "cookies": os.path.isfile(YT_COOKIES_FILE),
        "proxy": bool(PROXY),
    }


def _friendly(exc: Exception) -> str:
    msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*[\w-]+:\s*)?", "", str(exc)).strip()
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        missing = []
        if not pot_provider_status()["reachable"]:
            missing.append("start the PO token server")
        if not os.path.isfile(YT_COOKIES_FILE):
            missing.append("add YouTube cookies")
        if not PROXY:
            missing.append("use a residential proxy")
        hint = ("Try: " + ", ".join(missing) + ".") if missing else "This server's IP is flagged; try a different proxy."
        return f"YouTube blocked this server with a bot check. {hint} See README → YouTube bot check."
    if "age" in low and ("restrict" in low or "confirm your age" in low):
        return "This video is age-restricted. Add cookies from a signed-in YouTube account (see README)."
    if "private video" in low:
        return "This video is private."
    if "members-only" in low or "join this channel" in low:
        return "This video is for channel members only."
    if "video unavailable" in low or "not available" in low:
        return "This video is unavailable (deleted, blocked in the server's country, or private)."
    if any(s in low for s in ("connection", "proxy", "timed out", "name resolution")):
        return "Could not reach YouTube. Check the server's internet connection."
    return msg.splitlines()[0][:300] if msg else exc.__class__.__name__


# Thin wrappers around yt-dlp so tests can replace them.
def _extract_info(url: str) -> dict:
    import yt_dlp

    def attempt(clients):
        with yt_dlp.YoutubeDL(_base_opts(clients)) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=False))

    return _with_fallbacks(attempt)


def _run_download(url: str, opts: dict) -> None:
    import yt_dlp

    def attempt(clients):
        with yt_dlp.YoutubeDL({**_base_opts(clients), **opts}) as ydl:
            ydl.download([url])

    _with_fallbacks(attempt)


def resolve(raw: str) -> MediaResult:
    url = watch_url(raw)
    try:
        info = _extract_info(url)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(_friendly(exc)) from exc

    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise ExtractionError("Live streams and premieres can't be downloaded until they've ended.")
    duration = info.get("duration")
    if MAX_DURATION and duration and duration > MAX_DURATION:
        raise ExtractionError(f"Video is too long (limit is {MAX_DURATION // 60} minutes).")

    heights = {f.get("height") for f in info.get("formats") or []
               if f.get("vcodec") not in (None, "none") and f.get("height")}
    # snap odd heights (e.g. 1076) to the nearest standard label at or below
    qualities = sorted({next((s for s in STANDARD_HEIGHTS if h >= s), h) for h in heights}, reverse=True)

    vid = info.get("id") or video_id(raw)
    return MediaResult(
        source_url=url,
        kind="short" if "/shorts/" in raw else "video",
        platform="youtube",
        owner=info.get("uploader") or info.get("channel"),
        title=info.get("title"),
        caption=info.get("title"),
        shortcode=vid,
        duration=int(duration) if duration else None,
        qualities=qualities,
        backend="yt-dlp",
        items=[MediaItem(index=0, type="video", url=url,
                         thumbnail=info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")],
    )


def download(url: str, mode: str, outdir: str, quality: Optional[int] = None,
             progress: Optional[ProgressFn] = None) -> str:
    """Download `url` into `outdir` as mp4 (mode="video") or mp3 (mode="audio"). Returns the file path."""
    progress = progress or (lambda f, s: None)

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes") or 0) / total if total else 0.0
            is_audio = (d.get("info_dict") or {}).get("vcodec") in (None, "none")
            progress(min(frac, 1.0), "Downloading audio" if is_audio else "Downloading video")
        elif d.get("status") == "finished":
            progress(1.0, "Processing")

    def pp_hook(d):
        if d.get("status") == "started":
            progress(1.0, "Converting to MP3" if mode == "audio" else "Merging video and audio")

    opts = {
        "outtmpl": os.path.join(outdir, "media.%(ext)s"),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
    }
    if mode == "audio":
        opts.update({
            "format": "ba/b",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
        })
        want_ext = "mp3"
    else:
        sort = [f"res:{int(quality)}"] if quality else ["res"]
        opts.update({
            "format": "bv*+ba/b",
            # highest resolution up to `quality`, preferring H.264/AAC so the file plays everywhere
            "format_sort": [*sort, "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4",
        })
        want_ext = "mp4"

    try:
        _run_download(url, opts)
    except Exception as exc:
        raise ExtractionError(_friendly(exc)) from exc

    files = [f for f in os.listdir(outdir) if f.startswith("media.") and f.endswith("." + want_ext)]
    if not files:
        files = [f for f in os.listdir(outdir) if f.startswith("media.") and not f.endswith((".part", ".jpg", ".webp", ".png"))]
    if not files:
        raise ExtractionError("Download finished but no output file was produced.")
    return os.path.join(outdir, files[0])
