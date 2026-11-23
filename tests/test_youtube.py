import os
import subprocess
import time
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import extractor, main, updater, youtube
from app.extractor import ExtractionError

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"youtube.com/watch?v={VID}&list=PL123&t=42s",
    f"https://youtu.be/{VID}?si=abc",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://music.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/live/{VID}",
    f"https://www.youtube.com/embed/{VID}",
])
def test_video_id(url):
    assert youtube.is_youtube(url)
    assert youtube.video_id(url) == VID


@pytest.mark.parametrize("url", ["https://www.youtube.com/@somechannel", "https://www.youtube.com/playlist?list=PL1"])
def test_unsupported_youtube_links(url):
    with pytest.raises(ExtractionError, match="Unsupported YouTube link"):
        youtube.video_id(url)


def test_not_youtube():
    assert not youtube.is_youtube("https://www.instagram.com/p/abc/")
    assert not youtube.is_youtube("https://notyoutube.com/watch?v=" + VID)


FAKE_INFO = {
    "id": VID, "title": "Test Video: Part 1/2", "uploader": "Channel", "duration": 212,
    "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg",
    "formats": [
        {"height": 1080, "vcodec": "avc1"}, {"height": 720, "vcodec": "vp9"}, {"height": 1076, "vcodec": "av01"},
        {"height": 360, "vcodec": "avc1"}, {"vcodec": "none", "acodec": "opus"},
    ],
}


def test_resolve(monkeypatch):
    monkeypatch.setattr(youtube, "_extract_info", lambda url: FAKE_INFO)
    r = extractor.resolve(f"https://youtu.be/{VID}")
    assert (r.platform, r.title, r.duration, r.owner) == ("youtube", "Test Video: Part 1/2", 212, "Channel")
    assert r.qualities == [1080, 720, 360]  # 1076 snaps to 1080
    assert r.items[0].url == f"https://www.youtube.com/watch?v={VID}"


def test_resolve_rejects_live_and_long(monkeypatch):
    monkeypatch.setattr(youtube, "_extract_info", lambda url: {**FAKE_INFO, "is_live": True})
    with pytest.raises(ExtractionError, match="Live"):
        youtube.resolve(f"https://youtu.be/{VID}")
    monkeypatch.setattr(youtube, "_extract_info", lambda url: {**FAKE_INFO, "duration": 99999})
    with pytest.raises(ExtractionError, match="too long"):
        youtube.resolve(f"https://youtu.be/{VID}")


def test_friendly_bot_check(monkeypatch):
    def boom(url):
        raise Exception("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. Use --cookies")
    monkeypatch.setattr(youtube, "_extract_info", boom)
    with pytest.raises(ExtractionError, match="bot check"):
        youtube.resolve(f"https://youtu.be/{VID}")


def _fake_download(tmp_video):
    """Stand-in for yt-dlp: reports progress like it would and writes the output file."""
    def run(url, opts):
        hook = opts["progress_hooks"][0]
        for done in (0, 50, 100):
            hook({"status": "downloading", "downloaded_bytes": done, "total_bytes": 100, "info_dict": {"vcodec": "avc1"}})
            time.sleep(0.05)
        hook({"status": "finished"})
        run.opts = opts
        outdir = os.path.dirname(opts["outtmpl"])
        if any(p["key"] == "FFmpegExtractAudio" for p in opts.get("postprocessors", [])):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-i", tmp_video, "-vn", os.path.join(outdir, "media.mp3")], check=True)
        else:
            subprocess.run(["cp", tmp_video, os.path.join(outdir, "media.mp4")], check=True)
    return run


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64",
                    "-f", "lavfi", "-i", "sine=d=1", "-shortest", str(path)], check=True)
    return str(path)


def _wait(c, job):
    for _ in range(100):
        job = c.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.mark.parametrize("mode,quality,ctype,name", [
    ("video", 720, "video/mp4", "Test Video Part 12 (720p).mp4"),
    ("video", None, "video/mp4", "Test Video Part 12.mp4"),
    ("audio", None, "audio/mpeg", "Test Video Part 12.mp3"),
])
def test_job_end_to_end(monkeypatch, video_file, mode, quality, ctype, name):
    monkeypatch.setattr(youtube, "_extract_info", lambda url: FAKE_INFO)
    fake = _fake_download(video_file)
    monkeypatch.setattr(youtube, "_run_download", fake)
    c = TestClient(main.app)

    data = c.post("/api/resolve", json={"url": f"https://www.youtube.com/watch?v={VID}"}).json()
    assert data["platform"] == "youtube" and data["items"][0]["download_url"] is None
    assert c.get(f"/api/download/{data['token']}/0").status_code == 400  # IG-only route

    job = c.post("/api/jobs", json={"token": data["token"], "mode": mode, "quality": quality}).json()
    job = _wait(c, job)
    assert job["status"] == "done", job
    r = c.get(job["file_url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
    assert name in unquote(r.headers["content-disposition"]) and len(r.content) > 500
    if mode == "video":
        assert fake.opts["merge_output_format"] == "mp4"
        assert fake.opts["format_sort"][0] == (f"res:{quality}" if quality else "res")


def test_job_rejects_bad_quality(monkeypatch):
    monkeypatch.setattr(youtube, "_extract_info", lambda url: FAKE_INFO)
    c = TestClient(main.app)
    token = c.post("/api/resolve", json={"url": f"https://youtu.be/{VID}"}).json()["token"]
    assert c.post("/api/jobs", json={"token": token, "quality": 999}).status_code == 400


def test_job_error_is_reported(monkeypatch):
    monkeypatch.setattr(youtube, "_extract_info", lambda url: FAKE_INFO)

    def fail(url, opts):
        raise Exception("ERROR: [youtube] x: Private video. Sign in if you've been granted access")
    monkeypatch.setattr(youtube, "_run_download", fail)
    c = TestClient(main.app)
    token = c.post("/api/resolve", json={"url": f"https://youtu.be/{VID}"}).json()["token"]
    job = _wait(c, c.post("/api/jobs", json={"token": token}).json())
    assert job["status"] == "error" and job["error"] == "This video is private."


def test_updater_installs_ytdlp_with_extras(monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    updater.install({"yt-dlp": ("1", "2"), "instaloader": ("1", "2")})
    assert "yt-dlp[default]==2" in calls[0] and "instaloader==2" in calls[0]
