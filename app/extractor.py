"""Resolve an Instagram link into a list of downloadable media items.

Two backends are used:
  1. instaloader  - handles images, videos, carousels and stories well.
  2. yt-dlp       - fallback, very good at reels / videos.

Stories (and private content) require an authenticated session. Provide one
by exporting your browser cookies in Netscape format to a file and pointing
IG_COOKIES_FILE at it (default: ./cookies.txt).
"""

from __future__ import annotations

import http.cookiejar
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

COOKIES_FILE = os.environ.get("IG_COOKIES_FILE", "cookies.txt")

ALLOWED_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}

POST_RE = re.compile(r"^/(?:[\w.]+/)?(?P<kind>p|reel|reels|tv)/(?P<code>[\w-]+)")
STORY_ITEM_RE = re.compile(r"^/stories/(?P<user>[\w.]+)/(?P<id>\d+)")
HIGHLIGHT_RE = re.compile(r"^/stories/highlights/(?P<id>\d+)")
STORY_USER_RE = re.compile(r"^/stories/(?P<user>[\w.]+)/?$")


class ExtractionError(Exception):
    """Raised when media could not be resolved from a link."""


@dataclass
class MediaItem:
    index: int
    type: str  # "image" | "video"
    url: str
    thumbnail: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def ext(self) -> str:
        return "mp4" if self.type == "video" else "jpg"


@dataclass
class MediaResult:
    source_url: str
    kind: str  # "post" | "reel" | "story" | "highlight" | "video"
    platform: str = "instagram"  # "instagram" | "youtube"
    owner: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None  # seconds
    qualities: list[int] = field(default_factory=list)  # available video heights (YouTube)
    caption: Optional[str] = None
    shortcode: Optional[str] = None
    backend: Optional[str] = None
    items: list[MediaItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- #
# URL parsing
# --------------------------------------------------------------------------- #

@dataclass
class ParsedLink:
    url: str
    kind: str
    shortcode: Optional[str] = None
    username: Optional[str] = None
    media_id: Optional[str] = None


def parse_link(raw: str) -> ParsedLink:
    raw = (raw or "").strip()
    if not raw:
        raise ExtractionError("Please paste an Instagram link.")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ExtractionError("Please paste an Instagram or YouTube link.")
    path = parsed.path or "/"
    clean = f"https://www.instagram.com{path}"

    if m := POST_RE.match(path):
        kind = "reel" if m["kind"] in ("reel", "reels", "tv") else "post"
        return ParsedLink(clean, kind, shortcode=m["code"])
    if m := HIGHLIGHT_RE.match(path):
        return ParsedLink(clean, "highlight", media_id=m["id"])
    if m := STORY_ITEM_RE.match(path):
        return ParsedLink(clean, "story", username=m["user"], media_id=m["id"])
    if m := STORY_USER_RE.match(path):
        return ParsedLink(clean, "story", username=m["user"])
    raise ExtractionError(
        "Unsupported link. Use a post (/p/...), reel (/reel/...), or story (/stories/...) link."
    )


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #

def _cookie_dict() -> dict[str, str]:
    if not os.path.isfile(COOKIES_FILE):
        return {}
    jar = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError) as exc:
        log.warning("Could not load cookies from %s: %s", COOKIES_FILE, exc)
        return {}
    return {c.name: c.value for c in jar if "instagram.com" in c.domain}


def has_session() -> bool:
    return "sessionid" in _cookie_dict()


# --------------------------------------------------------------------------- #
# instaloader backend
# --------------------------------------------------------------------------- #

def _instaloader():
    import instaloader

    L = instaloader.Instaloader(
        quiet=True,
        download_comments=False,
        save_metadata=False,
        max_connection_attempts=1,
        request_timeout=30.0,
    )
    cookies = _cookie_dict()
    if "sessionid" in cookies:
        username = os.environ.get("IG_USERNAME") or cookies.get("ds_user_id", "user")
        L.load_session(username, cookies)
    return L


def _via_instaloader(link: ParsedLink) -> MediaResult:
    import instaloader

    L = _instaloader()
    ctx = L.context
    result = MediaResult(source_url=link.url, kind=link.kind, backend="instaloader")

    if link.shortcode:
        post = instaloader.Post.from_shortcode(ctx, link.shortcode)
        result.owner = post.owner_username
        result.caption = post.caption
        result.shortcode = post.shortcode
        if post.typename == "GraphSidecar":
            for i, node in enumerate(post.get_sidecar_nodes()):
                result.items.append(MediaItem(
                    index=i,
                    type="video" if node.is_video else "image",
                    url=node.video_url if node.is_video else node.display_url,
                    thumbnail=node.display_url,
                ))
        else:
            result.items.append(MediaItem(
                index=0,
                type="video" if post.is_video else "image",
                url=post.video_url if post.is_video else post.url,
                thumbnail=post.url,
            ))
        return result

    if link.kind == "story":
        if not ctx.is_logged_in:
            raise ExtractionError("Stories require login. Add your Instagram cookies (see README).")
        result.owner = link.username
        if link.media_id:
            story_items = [instaloader.StoryItem.from_mediaid(ctx, int(link.media_id))]
        else:
            profile = instaloader.Profile.from_username(ctx, link.username)
            story_items = [it for s in L.get_stories(userids=[profile.userid]) for it in s.get_items()]
        for i, it in enumerate(story_items):
            result.items.append(MediaItem(
                index=i,
                type="video" if it.is_video else "image",
                url=it.video_url if it.is_video else it.url,
                thumbnail=it.url,
            ))
        if not result.items:
            raise ExtractionError("No active stories found for this user.")
        return result

    raise ExtractionError("instaloader cannot handle this link type.")


# --------------------------------------------------------------------------- #
# yt-dlp backend
# --------------------------------------------------------------------------- #

def _ytdlp_entry_to_item(entry: dict, index: int) -> Optional[MediaItem]:
    url = entry.get("url")
    if not url and entry.get("formats"):
        url = entry["formats"][-1].get("url")
    if not url:
        return None
    is_video = entry.get("vcodec") not in (None, "none") or entry.get("ext") == "mp4"
    return MediaItem(
        index=index,
        type="video" if is_video else "image",
        url=url,
        thumbnail=entry.get("thumbnail"),
        width=entry.get("width"),
        height=entry.get("height"),
    )


def _via_ytdlp(link: ParsedLink) -> MediaResult:
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # best single file containing both audio and video, so it can be proxied directly
        "format": "best[vcodec!=none][acodec!=none]/best",
    }
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(link.url, download=False)
    info = ydl.sanitize_info(info) if info else None
    if not info:
        raise ExtractionError("No media found.")

    entries = info.get("entries") or [info]
    result = MediaResult(
        source_url=link.url,
        kind=link.kind,
        owner=info.get("uploader") or info.get("channel") or link.username,
        caption=info.get("description") or info.get("title"),
        shortcode=link.shortcode,
        backend="yt-dlp",
    )
    for e in entries:
        if e and (item := _ytdlp_entry_to_item(e, len(result.items))):
            result.items.append(item)
    if not result.items:
        raise ExtractionError("No downloadable media found.")
    return result


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _friendly(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "login" in low or "401" in low or "checkpoint" in low:
        return "Instagram requires login for this content. Add your cookies (see README)."
    if "not found" in low or "404" in low or "does not exist" in low:
        return "Post not found. It may be deleted or private."
    if "429" in low or "rate" in low or "please wait" in low:
        return "Instagram is rate-limiting requests. Try again in a few minutes."
    if any(s in low for s in ("connection", "proxy", "timed out", "name resolution", "max retries")):
        return "Could not reach Instagram. Check the server's internet connection."
    return msg.splitlines()[0][:300] if msg else exc.__class__.__name__


def resolve(raw_url: str) -> MediaResult:
    from . import youtube

    if youtube.is_youtube(raw_url):
        return youtube.resolve(raw_url)
    link = parse_link(raw_url)
    errors: list[str] = []
    for backend in (_via_instaloader, _via_ytdlp):
        try:
            result = backend(link)
            if result.items:
                return result
        except ExtractionError as exc:
            errors.append(str(exc))
        except Exception as exc:  # backends raise a wide variety of errors
            log.info("%s failed: %s", backend.__name__, exc)
            errors.append(_friendly(exc))
    raise ExtractionError(errors[0] if errors else "Could not fetch media.")
