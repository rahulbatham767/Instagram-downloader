import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from app import extractor, main
from app.extractor import ExtractionError, MediaItem, MediaResult, parse_link


@pytest.mark.parametrize("url,kind,code,user,mid", [
    ("https://www.instagram.com/p/ABC123xyz/", "post", "ABC123xyz", None, None),
    ("instagram.com/reel/C-d_9/?igsh=abc", "reel", "C-d_9", None, None),
    ("https://www.instagram.com/someuser/reel/XYZ/", "reel", "XYZ", None, None),
    ("https://instagram.com/tv/TVcode", "reel", "TVcode", None, None),
    ("https://www.instagram.com/stories/nasa/3141592653589793/", "story", None, "nasa", "3141592653589793"),
    ("https://www.instagram.com/stories/nasa/", "story", None, "nasa", None),
    ("https://www.instagram.com/stories/highlights/1789/", "highlight", None, None, "1789"),
])
def test_parse_link(url, kind, code, user, mid):
    link = parse_link(url)
    assert (link.kind, link.shortcode, link.username, link.media_id) == (kind, code, user, mid)


@pytest.mark.parametrize("url", ["", "https://youtube.com/watch?v=1", "https://www.instagram.com/nasa/"])
def test_parse_link_rejects(url):
    with pytest.raises(ExtractionError):
        parse_link(url)


def test_resolve_falls_back_to_ytdlp(monkeypatch):
    def boom(link):
        raise RuntimeError("401 Unauthorized - login required")

    def ok(link):
        return MediaResult(link.url, link.kind, items=[MediaItem(0, "video", "https://x.cdninstagram.com/v.mp4")])

    monkeypatch.setattr(extractor, "_via_instaloader", boom)
    monkeypatch.setattr(extractor, "_via_ytdlp", ok)
    assert extractor.resolve("https://www.instagram.com/reel/abc/").items[0].type == "video"


def test_resolve_reports_friendly_error(monkeypatch):
    def boom(link):
        raise RuntimeError("401 Unauthorized - login required")

    monkeypatch.setattr(extractor, "_via_instaloader", boom)
    monkeypatch.setattr(extractor, "_via_ytdlp", boom)
    with pytest.raises(ExtractionError, match="requires login"):
        extractor.resolve("https://www.instagram.com/p/abc/")


@pytest.fixture
def client(monkeypatch):
    result = MediaResult("https://www.instagram.com/p/abc/", "post", owner="nasa", shortcode="abc", items=[
        MediaItem(0, "image", "https://scontent.cdninstagram.com/a.jpg"),
        MediaItem(1, "video", "https://scontent.cdninstagram.com/b.mp4"),
    ])
    monkeypatch.setattr(extractor, "resolve", lambda url: result)
    return TestClient(main.app)


def test_resolve_endpoint_and_image_download(client, monkeypatch):
    data = client.post("/api/resolve", json={"url": "https://www.instagram.com/p/abc/"}).json()
    assert [i["type"] for i in data["items"]] == ["image", "video"]
    assert data["items"][0]["audio_url"] is None and data["items"][1]["audio_url"]
    assert "url" not in data["items"][0]

    def handler(request):
        assert request.url.host == "scontent.cdninstagram.com"
        return httpx.Response(200, content=b"JPEGDATA", headers={"content-type": "image/jpeg"})

    real = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = client.get(data["items"][0]["download_url"])
    assert r.status_code == 200 and r.content == b"JPEGDATA"
    assert 'filename="nasa_abc_1.jpg"' in r.headers["content-disposition"]


def test_expired_token(client):
    assert client.get("/api/download/nope/0").status_code == 410


def test_rejects_non_cdn_host(monkeypatch):
    result = MediaResult("u", "post", items=[MediaItem(0, "image", "https://evil.example.com/a.jpg")])
    monkeypatch.setattr(extractor, "resolve", lambda url: result)
    c = TestClient(main.app)
    data = c.post("/api/resolve", json={"url": "x"}).json()
    assert c.get(data["items"][0]["download_url"]).status_code == 400


def test_audio_extraction(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64",
                    "-f", "lavfi", "-i", "sine=d=1", "-shortest", str(video)], check=True)
    result = MediaResult("u", "reel", owner="me", shortcode="r1", items=[MediaItem(0, "video", str(video))])
    monkeypatch.setattr(extractor, "resolve", lambda url: result)
    monkeypatch.setattr(main, "_check_cdn", lambda url: None)
    c = TestClient(main.app)
    data = c.post("/api/resolve", json={"url": "x"}).json()
    r = c.get(data["items"][0]["audio_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert len(r.content) > 1000 and 'me_r1.mp3' in r.headers["content-disposition"]


def test_frontend_served():
    r = TestClient(main.app).get("/")
    assert r.status_code == 200 and "InstaSave" in r.text
