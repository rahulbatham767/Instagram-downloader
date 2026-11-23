#!/bin/sh
# Container entrypoint: start the PO token server, then the web app.
set -e
mkdir -p data

# On Hugging Face, add your cookies.txt contents as a Space secret named COOKIES_TXT
# (Settings -> Variables and secrets). It's written to a file the app reads.
if [ -n "$COOKIES_TXT" ]; then
  printf '%s\n' "$COOKIES_TXT" > "$IG_COOKIES_FILE"
fi
if [ -n "$YT_COOKIES_TXT" ]; then
  printf '%s\n' "$YT_COOKIES_TXT" > data/youtube_cookies.txt
  export YT_COOKIES_FILE="$PWD/data/youtube_cookies.txt"
fi

# YouTube PO token server (bgutil), restarted automatically if it ever exits.
POT=/home/user/pot-provider
(
  cd "$POT"
  while true; do
    deno run --allow-env --allow-net --allow-ffi="$POT/node_modules" --allow-read="$POT/node_modules" \
      src/main.ts --host 127.0.0.1 --port 4416 || true
    echo "PO token server exited; restarting in 5s" >&2
    sleep 5
  done
) &

exec python -m app --host 0.0.0.0 --port "${PORT:-7860}"
