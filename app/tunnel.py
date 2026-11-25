"""Find the public Cloudflare quick-tunnel link and announce it.

docker-compose.yml runs cloudflared next to the app. cloudflared reports the
random https://<words>.trycloudflare.com hostname on its metrics server at
GET /quicktunnel, so we poll that and print the link when it appears or changes.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx

log = logging.getLogger("uvicorn.error")

METRICS_URL = os.environ.get("TUNNEL_METRICS_URL", "").rstrip("/")
POLL_SECONDS = 5

state: dict = {"public_url": None}


def fetch_hostname() -> str | None:
    r = httpx.get(f"{METRICS_URL}/quicktunnel", timeout=3, trust_env=False)
    r.raise_for_status()
    return (r.json().get("hostname") or "").strip() or None


def announce(url: str) -> None:
    bar = "=" * (len(url) + 22)
    log.info("\n%s\n   PUBLIC LINK:  %s\n%s\n   Share it to open the app from any phone or computer.\n"
             "   (The link changes whenever the tunnel restarts.)\n%s", bar, url, bar, bar)


def _loop() -> None:
    while True:
        try:
            host = fetch_hostname()
            url = f"https://{host}" if host else None
            if url and url != state["public_url"]:
                state["public_url"] = url
                announce(url)
        except Exception:
            pass  # tunnel not up yet (or restarting); try again shortly
        time.sleep(POLL_SECONDS)


def start() -> None:
    if METRICS_URL:
        threading.Thread(target=_loop, name="tunnel-watch", daemon=True).start()
