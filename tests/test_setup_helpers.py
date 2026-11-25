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
    monkeypatch.setattr(potserver, "ensure_installed", lambda v, rt: pytest.fail("must not install"))
    potserver._main()
    assert potserver.state["status"] == "running"


def test_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(potserver.youtube, "POT_PROVIDER_URL", "http://127.0.0.1:4416")
    monkeypatch.setattr(potserver, "plugin_version", lambda: "2.0.2")
    monkeypatch.setattr(potserver.youtube, "pot_provider_status", lambda: {"reachable": False, "version": None})

    def boom(v, rt):
        raise RuntimeError("download failed: offline")

    monkeypatch.setattr(potserver, "runtime", lambda: "node")
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


# ------------------------------------------------- Termux / no-Deno mode ---

def _which(available):
    return lambda name: f"/bin/{name}" if name in available else None


def test_runtime_prefers_deno_then_node(monkeypatch):
    monkeypatch.setattr(potserver.shutil, "which", _which({"deno", "node", "npm"}))
    assert potserver.runtime() == "deno"
    monkeypatch.setattr(potserver.shutil, "which", _which({"node", "npm"}))
    assert potserver.runtime() == "node"
    monkeypatch.setattr(potserver.shutil, "which", _which(set()))
    with pytest.raises(RuntimeError, match="nodejs-lts"):
        potserver.runtime()


def test_server_command_for_each_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(potserver.shutil, "which", _which({"deno", "node"}))
    deno_cmd = potserver.server_command(tmp_path, "deno", 4416)
    assert deno_cmd[:2] == ["/bin/deno", "run"] and "src/main.ts" in deno_cmd
    assert potserver.server_command(tmp_path, "node", 4416) == [
        "/bin/node", "build/main.js", "--host", "127.0.0.1", "--port", "4416"]


def test_node_install_skips_native_canvas_build(monkeypatch, tmp_path):
    monkeypatch.setattr(potserver, "CACHE", tmp_path)
    monkeypatch.setattr(potserver.shutil, "which", _which({"npm"}))
    monkeypatch.setattr(potserver, "_download_source", lambda v, dest: (dest / "server").mkdir(parents=True))
    cmds = []
    monkeypatch.setattr(potserver, "_sub", lambda cmd, cwd, timeout: cmds.append(cmd))
    server = potserver.ensure_installed("2.0.2", "node")
    assert server == tmp_path / "2.0.2-node" / "server"
    assert cmds[0][:2] == ["/bin/npm", "ci"] and "--ignore-scripts" in cmds[0]
    assert cmds[1][-1] == "tsc"
    assert potserver.ensure_installed("2.0.2", "node") == server and len(cmds) == 2  # cached


def test_ytdlp_uses_node_when_deno_missing(monkeypatch):
    from app import youtube
    monkeypatch.setattr("shutil.which", _which({"node"}))
    assert youtube.js_runtimes() == {"node": {}}
    assert youtube._base_opts()["js_runtimes"] == {"node": {}}
    monkeypatch.setattr("shutil.which", _which({"deno", "node"}))
    assert youtube.js_runtimes() is None and "js_runtimes" not in youtube._base_opts()


def test_termux_ffmpeg_hint(monkeypatch, caplog):
    monkeypatch.setattr(ffmpeg_setup.shutil, "which", lambda name: None)
    monkeypatch.setenv("TERMUX_VERSION", "0.119")
    assert ffmpeg_setup.ensure_ffmpeg() is None
    assert "pkg install ffmpeg" in caplog.text


def test_core_requirements_have_no_desktop_only_packages():
    from pathlib import Path
    core = Path("requirements-core.txt").read_text()
    assert "deno" not in core and "imageio-ffmpeg" not in core and "uvicorn[standard]" not in core
    full = Path("requirements.txt").read_text()
    assert "-r requirements-core.txt" in full and "deno" in full
