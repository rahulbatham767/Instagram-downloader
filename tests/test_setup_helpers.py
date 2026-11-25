import io
import os
import sys
import tarfile

import pytest

from app import ffmpeg_setup, potserver


# ---------------------------------------------------------------- ffmpeg ---

def test_uses_system_ffmpeg_when_present(monkeypatch):
    monkeypatch.setattr(ffmpeg_setup.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert ffmpeg_setup.ensure_ffmpeg() == "/usr/bin/ffmpeg"


def test_installs_bundled_ffmpeg_when_missing(monkeypatch, tmp_path):
    fake_bin = tmp_path / "ffmpeg-linux-x86_64-v9"
    fake_bin.write_text("#!/bin/sh\necho fake ffmpeg\n")
    bindir = tmp_path / "bin"
    monkeypatch.setattr(ffmpeg_setup, "BIN_DIR", bindir)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", type("M", (), {"get_ffmpeg_exe": staticmethod(lambda: str(fake_bin))}))
    monkeypatch.setenv("PATH", "/nonexistent")
    path = ffmpeg_setup.ensure_ffmpeg()
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    assert path == str(bindir / name)
    assert os.access(path, os.X_OK)
    assert os.environ["PATH"].startswith(str(bindir))


# ----------------------------------------------------------- pot server ---

def test_local_port(monkeypatch):
    monkeypatch.setattr(potserver.youtube, "POT_PROVIDER_URL", "http://127.0.0.1:4416")
    assert potserver._local_port() == 4416
    monkeypatch.setattr(potserver.youtube, "POT_PROVIDER_URL", "http://pot-provider:4416")
    assert potserver._local_port() is None  # someone else's server; don't manage it


def test_skips_when_server_already_running(monkeypatch):
    monkeypatch.setattr(potserver.youtube, "POT_PROVIDER_URL", "http://127.0.0.1:4416")
    monkeypatch.setattr(potserver, "plugin_version", lambda: "2.0.2")
    monkeypatch.setattr(potserver.youtube, "pot_provider_status", lambda: {"reachable": True, "version": "2.0.2"})
    monkeypatch.setattr(potserver, "ensure_installed", lambda v: pytest.fail("must not install"))
    potserver._main()
    assert potserver.state["status"] == "running"


def test_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(potserver.youtube, "POT_PROVIDER_URL", "http://127.0.0.1:4416")
    monkeypatch.setattr(potserver, "plugin_version", lambda: "2.0.2")
    monkeypatch.setattr(potserver.youtube, "pot_provider_status", lambda: {"reachable": False, "version": None})

    def boom(v):
        raise RuntimeError("download failed: offline")

    monkeypatch.setattr(potserver, "ensure_installed", boom)
    potserver._main()
    assert potserver.state == {"status": "failed", "error": "download failed: offline"}


def _tarball(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def test_download_source_extracts_tarball(monkeypatch, tmp_path):
    data = _tarball({"bgutil-2.0.2/server/src/main.ts": b"// server"})
    monkeypatch.setattr(potserver.httpx, "get", lambda *a, **k: _Resp(data))
    dest = tmp_path / "2.0.2"
    potserver._download_source("2.0.2", dest)
    assert (dest / "server" / "src" / "main.ts").read_text() == "// server"


def test_download_source_rejects_path_traversal(monkeypatch, tmp_path):
    data = _tarball({"../../evil.txt": b"x"})
    monkeypatch.setattr(potserver.httpx, "get", lambda *a, **k: _Resp(data))
    monkeypatch.setattr(potserver.shutil, "which", lambda name: None)  # no git fallback
    with pytest.raises(RuntimeError):
        potserver._download_source("2.0.2", tmp_path / "2.0.2")
    assert not (tmp_path.parent / "evil.txt").exists()
