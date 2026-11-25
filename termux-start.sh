#!/usr/bin/env bash
# Run InstaSave on Android with Termux - one command, every time:
#
#     bash termux-start.sh            open http://localhost:8000 in your phone's browser
#     bash termux-start.sh --public   also get a public https://....trycloudflare.com link
#
# The first run installs everything it needs (ffmpeg, Node.js, Python packages).
# Some Python packages are built from source on Android, so the first run can take
# 10-20 minutes. Later runs start in seconds.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
STAMP=".termux-setup-done"
PKGS=(python ffmpeg nodejs-lts git clang rust binutils)

need_setup() {
  [ ! -f "$STAMP" ] && return 0
  # re-run setup when the Python requirements change (e.g. after git pull)
  [ "$(cat "$STAMP")" != "$(sha256sum requirements-core.txt | cut -d' ' -f1)" ]
}

if need_setup; then
  echo ">>> Setting up (first run or requirements changed). This can take 10-20 minutes."
  # Termux is a rolling release: upgrade first so new packages match the installed ones.
  apt-get update
  apt-get -y -o Dpkg::Options::=--force-confold full-upgrade
  apt-get -y -o Dpkg::Options::=--force-confold install "${PKGS[@]}"
  # Rust-based packages (pydantic-core) need the Android API level to build.
  ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || true)"
  export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-24}"
  pip install --upgrade -r requirements-core.txt
  sha256sum requirements-core.txt | cut -d' ' -f1 > "$STAMP"
  echo ">>> Setup finished."
fi

# Keep running while the screen is off (needs the Termux:API app for full effect).
command -v termux-wake-lock >/dev/null && termux-wake-lock || true

TUNNEL_PID=""
if [ "${1:-}" = "--public" ]; then
  if ! command -v cloudflared >/dev/null; then
    apt-get -y install cloudflared || echo "!!! Could not install cloudflared; continuing without a public link."
  fi
  if command -v cloudflared >/dev/null; then
    cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:20241 --url "http://127.0.0.1:$PORT" \
      > cloudflared.log 2>&1 &
    TUNNEL_PID=$!
    export TUNNEL_METRICS_URL="http://127.0.0.1:20241"  # the app prints the public link
  fi
fi
cleanup() { [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true; }
trap cleanup EXIT

echo ">>> Starting InstaSave: open http://localhost:$PORT in your browser (Ctrl+C to stop)"
python -m app --host 127.0.0.1 --port "$PORT"
