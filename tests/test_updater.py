import subprocess

from fastapi.testclient import TestClient

from app import main, updater


def test_check_and_update_installs_newer(monkeypatch):
    monkeypatch.setattr(updater, "current_versions", lambda: {"yt-dlp": "2024.1.1", "instaloader": "4.16"})
    monkeypatch.setattr(updater, "latest_version", lambda p: {"yt-dlp": "2026.11.1", "instaloader": "4.16"}[p])
    installed = []
    monkeypatch.setattr(updater, "install", lambda pkgs: installed.append(pkgs))
    monkeypatch.setattr(updater, "RESTART", True)
    monkeypatch.setitem(updater.state, "restart_pending", False)

    assert updater.check_and_update() == {"updated": {"yt-dlp": "2026.11.1"}}
    assert installed == [{"yt-dlp": ("2024.1.1", "2026.11.1")}]
    assert updater.state["restart_pending"] is True


def test_check_and_update_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "current_versions", lambda: {"yt-dlp": "2026.11.1"})
    monkeypatch.setattr(updater, "latest_version", lambda p: "2026.11.1")
    monkeypatch.setattr(updater, "install", lambda pkgs: (_ for _ in ()).throw(AssertionError("no install")))
    assert updater.check_and_update() == {"updated": {}}
    assert updater.state["last_result"] == "up to date"


def test_pip_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(updater, "outdated", lambda: {"yt-dlp": ("1", "2")})

    def fail(pkgs):
        raise subprocess.CalledProcessError(1, "pip", stderr=b"ERROR: no permission")

    monkeypatch.setattr(updater, "install", fail)
    assert "pip failed: ERROR: no permission" in updater.check_and_update()["error"]


def test_network_failure_is_reported(monkeypatch):
    monkeypatch.setattr(updater, "latest_version", lambda p: (_ for _ in ()).throw(OSError("offline")))
    assert "check failed" in updater.check_and_update()["error"]


def test_restart_argv(monkeypatch):
    monkeypatch.setattr(updater.sys, "argv", ["/srv/app/__main__.py", "--port", "9000"])
    assert updater._original_argv() == ["-m", "app", "--port", "9000"]
    monkeypatch.setattr(updater.sys, "argv", ["/usr/bin/uvicorn", "app.main:app"])
    assert updater._original_argv() == ["/usr/bin/uvicorn", "app.main:app"]


def test_failed_resolve_triggers_check_once(monkeypatch):
    from app import extractor
    from app.extractor import ExtractionError

    monkeypatch.setattr(updater, "ENABLED", True)
    monkeypatch.setitem(updater.state, "last_check", None)
    updater._wake.clear()
    monkeypatch.setattr(extractor, "resolve", lambda url: (_ for _ in ()).throw(ExtractionError("broken")))
    c = TestClient(main.app)
    assert c.post("/api/resolve", json={"url": "https://www.instagram.com/p/abc/"}).status_code == 422
    assert updater._wake.is_set()
    updater._wake.clear()
    # invalid links don't trigger update checks
    assert c.post("/api/resolve", json={"url": "https://example.com/"}).status_code == 422
    assert not updater._wake.is_set()


def test_active_request_counter_returns_to_zero():
    c = TestClient(main.app)
    c.get("/api/status")
    c.get("/api/download/missing/0")
    assert updater._active_requests == 0
    assert "versions" in c.get("/api/status").json()["updater"]
