import http.server
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import main, tunnel


@pytest.fixture
def fake_cloudflared():
    """Mimics cloudflared's metrics server: GET /quicktunnel -> {"hostname": "..."}."""
    current = {"hostname": ""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(current).encode() if self.path == "/quicktunnel" else b"404"
            self.send_response(200 if self.path == "/quicktunnel" else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", current
    srv.shutdown()


def test_fetch_hostname(monkeypatch, fake_cloudflared):
    url, current = fake_cloudflared
    monkeypatch.setattr(tunnel, "METRICS_URL", url)
    assert tunnel.fetch_hostname() is None  # tunnel still starting
    current["hostname"] = "happy-blue-words.trycloudflare.com"
    assert tunnel.fetch_hostname() == "happy-blue-words.trycloudflare.com"


def test_loop_announces_and_status_shows_link(monkeypatch, fake_cloudflared, caplog):
    url, current = fake_cloudflared
    current["hostname"] = "happy-blue-words.trycloudflare.com"
    monkeypatch.setattr(tunnel, "METRICS_URL", url)
    monkeypatch.setitem(tunnel.state, "public_url", None)
    calls = []
    monkeypatch.setattr(tunnel, "announce", calls.append)

    def stop(_):
        raise SystemExit

    monkeypatch.setattr(tunnel.time, "sleep", stop)
    with pytest.raises(SystemExit):
        tunnel._loop()
    assert calls == ["https://happy-blue-words.trycloudflare.com"]
    assert TestClient(main.app).get("/api/status").json()["public_url"] == calls[0]


def test_disabled_without_metrics_url(monkeypatch):
    monkeypatch.setattr(tunnel, "METRICS_URL", "")
    started = []
    monkeypatch.setattr(tunnel.threading, "Thread", lambda **kw: started.append(kw))
    tunnel.start()
    assert started == []
