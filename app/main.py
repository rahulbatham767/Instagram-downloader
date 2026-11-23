"""FastAPI web app: paste an Instagram link, download its video, image or audio."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import extractor
from .extractor import ExtractionError, MediaItem, MediaResult

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
CACHE_TTL = 30 * 60  # resolved CDN links expire, keep them for 30 minutes
CDN_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

app = FastAPI(title="Instagram Downloader")
_cache: dict[str, tuple[float, MediaResult]] = {}


class ResolveRequest(BaseModel):
    url: str


def _put(result: MediaResult) -> str:
    now = time.time()
    for key in [k for k, (ts, _) in _cache.items() if now - ts > CACHE_TTL]:
        _cache.pop(key, None)
    token = secrets.token_urlsafe(12)
    _cache[token] = (now, result)
    return token


def _get(token: str, index: int) -> tuple[MediaResult, MediaItem]:
    entry = _cache.get(token)
    if not entry or time.time() - entry[0] > CACHE_TTL:
        raise HTTPException(410, "This link has expired. Please fetch it again.")
    result = entry[1]
    if not 0 <= index < len(result.items):
        raise HTTPException(404, "Media item not found.")
    return result, result.items[index]


def _check_cdn(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme != "https" or not host.endswith(CDN_SUFFIXES):
        raise HTTPException(400, "Refusing to fetch media from an unexpected host.")


def _filename(result: MediaResult, item: MediaItem, ext: str) -> str:
    base = "_".join(p for p in (result.owner, result.shortcode or result.kind) if p) or "instagram"
    base = re.sub(r"[^\w.-]+", "_", base)[:80]
    suffix = f"_{item.index + 1}" if len(result.items) > 1 else ""
    return f"{base}{suffix}.{ext}"


@app.get("/api/status")
def status():
    return {"logged_in": extractor.has_session(), "ffmpeg": shutil.which("ffmpeg") is not None}


@app.post("/api/resolve")
async def resolve(req: ResolveRequest):
    try:
        result = await run_in_threadpool(extractor.resolve, req.url)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    token = _put(result)
    data = result.to_dict()
    data["token"] = token
    for item in data["items"]:
        i = item["index"]
        item["download_url"] = f"/api/download/{token}/{i}"
        item["preview_url"] = f"/api/download/{token}/{i}?inline=1"
        item["audio_url"] = f"/api/audio/{token}/{i}" if item["type"] == "video" else None
        item.pop("url", None)  # raw CDN url is not needed by the client
    return data


@app.get("/api/download/{token}/{index}")
async def download(token: str, index: int, inline: bool = Query(False)):
    result, item = _get(token, index)
    _check_cdn(item.url)

    client = httpx.AsyncClient(follow_redirects=True, timeout=60, headers={"User-Agent": USER_AGENT})
    req = client.build_request("GET", item.url)
    upstream = await client.send(req, stream=True)
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, f"Instagram CDN returned {upstream.status_code}. Try fetching again.")

    async def close():
        await upstream.aclose()
        await client.aclose()

    ctype = upstream.headers.get("content-type", "video/mp4" if item.type == "video" else "image/jpeg")
    ext = "mp4" if "video" in ctype else ("webp" if "webp" in ctype else ("png" if "png" in ctype else "jpg"))
    headers = {}
    if not inline:
        headers["Content-Disposition"] = f'attachment; filename="{_filename(result, item, ext)}"'
    if cl := upstream.headers.get("content-length"):
        headers["Content-Length"] = cl
    return StreamingResponse(upstream.aiter_bytes(), media_type=ctype, headers=headers,
                             background=BackgroundTask(close))


@app.get("/api/audio/{token}/{index}")
async def audio(token: str, index: int):
    result, item = _get(token, index)
    if item.type != "video":
        raise HTTPException(400, "Audio can only be extracted from videos.")
    if not shutil.which("ffmpeg"):
        raise HTTPException(500, "ffmpeg is not installed on the server.")
    _check_cdn(item.url)

    tmp = tempfile.mkdtemp(prefix="igdl_")
    out = os.path.join(tmp, "audio.mp3")
    ua = ["-user_agent", USER_AGENT] if item.url.startswith("http") else []
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y", *ua, "-i", item.url,
        "-vn", "-acodec", "libmp3lame", "-q:a", "2", out,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(504, "Audio extraction timed out.")
    ok = proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
    if not ok:
        shutil.rmtree(tmp, ignore_errors=True)
        msg = (err or b"").decode(errors="ignore").lower()
        if "does not contain any stream" in msg or "output file is empty" in msg or "matches no streams" in msg:
            raise HTTPException(422, "This video has no audio track.")
        raise HTTPException(502, "Could not extract audio from this video.")
    return FileResponse(out, media_type="audio/mpeg", filename=_filename(result, item, "mp3"),
                        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
