# InstaSave — Instagram & YouTube Downloader

Paste an Instagram or YouTube link and download what's in it:

| Content | Link example | Output |
|---|---|---|
| Reels / videos | `instagram.com/reel/<code>/`, `/tv/<code>/` | MP4 |
| Photos & carousels | `instagram.com/p/<code>/` | JPG / MP4 per slide |
| Stories (one item or all of a user's) | `instagram.com/stories/<user>/<id>/`, `/stories/<user>/` | JPG / MP4 |
| Audio | any Instagram video above | MP3 (via ffmpeg) |
| YouTube videos & Shorts | `youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/…`, `music.youtube.com/…` | MP4 (choose 144p – 4K) or MP3 |

YouTube streams the best qualities as separate video and audio, so for YouTube the server downloads
and merges them with ffmpeg (or converts to MP3) as a background job. The page shows a progress bar
and the file downloads when it's ready. It prefers H.264/AAC so the MP4 plays everywhere; above 1080p
YouTube only offers VP9/AV1, which modern players handle fine.

It's a FastAPI backend with a single-page web UI. It uses
[instaloader](https://instaloader.github.io/) first and falls back to [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Run it

Requirements: Python 3.10+, and `ffmpeg` on your PATH (for MP3 and YouTube).
`requirements.txt` also installs **Deno** and **yt-dlp-ejs**, which yt-dlp needs to read YouTube.

```bash
pip install -r requirements.txt
python -m app --port 8000
```

Open http://localhost:8000, paste a link, and click **Download**.
Tip: `http://localhost:8000/?url=<instagram link>` fetches the link right away.

With Docker:

```bash
docker compose up -d --build   # app + YouTube PO token server; put cookies in ./data/
```

### Command line

```bash
python -m app.cli https://www.instagram.com/reel/XXXX/           # save the video
python -m app.cli https://www.instagram.com/reel/XXXX/ --audio   # save as MP3
python -m app.cli https://www.instagram.com/p/XXXX/ -o my_folder # every carousel item
python -m app.cli https://youtu.be/XXXX --quality 1080           # YouTube video, max 1080p
python -m app.cli https://youtu.be/XXXX --audio                  # YouTube as MP3
```

## Automatic updates

Instagram and YouTube change their sites often, and when they do, downloads break until yt-dlp / instaloader
release a fix (usually within days). The server keeps those two libraries current by itself:

- It checks PyPI **at startup and every 6 hours**, and also **soon after a download fails** (at most once an hour).
- If a newer version exists it runs `pip install`, waits for in-progress downloads to finish (up to 15 min),
  and **restarts itself** so the new code is loaded.
- `GET /api/status` shows the installed versions and the result of the last check.

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_UPDATE` | `1` | set to `0` to turn the updater off |
| `AUTO_UPDATE_INTERVAL_HOURS` | `6` | how often to check |
| `AUTO_UPDATE_RESTART` | `1` | set to `0` to install updates but apply them only on the next manual restart |

Notes:
- Start the server with `python -m app` (or `uvicorn app.main:app` with a **single** worker). Don't use
  `--reload` or `--workers`, because the restart replaces the current process.
- pip needs write access to the Python environment the server runs in (a virtualenv you own is ideal).
- In Docker, updates live inside the running container and are lost when it's recreated, but the
  updater re-applies them a few seconds after every start.
- The repo also has a **Dependabot** config (`.github/dependabot.yml`). Once the repo is on GitHub with Dependabot
  enabled, it opens a pull request whenever a new version of a dependency or the Docker base image is released.

## YouTube bot check ("Sign in to confirm you're not a bot")

YouTube shows this to traffic it thinks is automated, and **cloud/datacenter IPs (VPS, AWS, etc.) get it far
more often than home connections.** No single trick fixes it every time, so the app layers several defenses.
They're listed below from most to least important.

**1. PO token provider (set up automatically with Docker).**
The [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide) recommends a PO-token
provider plugin, and this app uses [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
(maintained by a yt-dlp maintainer). The plugin is in `requirements.txt` and kept updated by the auto-updater. It needs
its small token server running:

```bash
docker compose up -d --build        # app + token server, nothing else to do
# or, without compose, run just the token server next to `python -m app`:
docker run -d --name bgutil-provider --init --restart unless-stopped \
  -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider
```

Keep the `127.0.0.1:` in the port mapping: the token server has no authentication and shouldn't be public.
`GET /api/status` → `youtube.pot_provider.reachable` tells you if the app can see it.

**2. Cookies from a throwaway account** (needed when your server's IP itself is flagged; a PO token alone doesn't
get past IP-based blocks). Follow yt-dlp's recommended way so YouTube doesn't rotate them away:
1. Open a **private/incognito window** and log into YouTube with a **spare** account (never your main one: accounts
   used for downloading can be temporarily or permanently banned).
2. In that same tab go to `https://www.youtube.com/robots.txt`.
3. Export cookies with an extension such as "Get cookies.txt LOCALLY" to `data/cookies.txt` (Docker) or
   `cookies.txt`, then **close the private window without logging out**.

**3. A residential proxy** if the server's IP is blocked outright. Set `YT_PROXY`
(e.g. `socks5://user:pass@host:1080`). Home connections rarely need this.

**4. Automatic fallbacks (built in).** On a bot check or HTTP 403 the app retries with other YouTube clients
(`tv`, `web_safari`, `mweb`, then `android_vr`, `web_embedded`, some of which need no PO token).
And the auto-updater installs yt-dlp fixes as soon as they're released.

The error message in the app tells you which of these are still missing.

| Variable | Default | Meaning |
|---|---|---|
| `YT_POT_PROVIDER_URL` | `http://127.0.0.1:4416` | PO token server (compose sets `http://pot-provider:4416`) |
| `YT_COOKIES_FILE` | same as `IG_COOKIES_FILE` | cookies used for YouTube |
| `YT_PROXY` | none | proxy for YouTube requests |
| `YT_PLAYER_CLIENTS` | `default,mweb` | yt-dlp player clients to use first |
| `YT_MAX_DURATION_MINUTES` | `180` | refuse longer videos (`0` = no limit) |
| `MAX_CONCURRENT_JOBS` | `2` | YouTube downloads processed at the same time (others wait in a queue) |

Rate limits: YouTube allows about 300 videos/hour without an account and about 2000/hour with one. Beyond that you'll
see "This content isn't available, try again later".

Finished YouTube files are kept on the server for 30 minutes and then deleted.

## Stories and private posts (login)

Instagram only shows stories, and often reels, to logged-in users. To enable them:

1. Log in to instagram.com in your browser.
2. Export the cookies in **Netscape format** with an extension such as "Get cookies.txt LOCALLY".
3. Save the file as `cookies.txt` in the project folder, or point `IG_COOKIES_FILE` at it.
4. Restart the server. The yellow notice in the UI goes away once a `sessionid` cookie is found.

`cookies.txt` is in `.gitignore`. Never commit it or share it, because it gives access to your account.
Use a secondary account if you can: heavy automated use can get an account rate-limited or flagged.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/resolve` | body `{"url": "..."}` → owner, caption and items (each with `download_url`, `preview_url`, `audio_url`) |
| `GET` | `/api/download/{token}/{index}` | streams the image or video (`?inline=1` for preview) |
| `GET` | `/api/audio/{token}/{index}` | MP3 extracted from a video |
| `POST` | `/api/jobs` | YouTube: body `{"token", "mode": "video"\|"audio", "quality": 720}` → job |
| `GET` | `/api/jobs/{id}` | job status, progress and `file_url` when done |
| `GET` | `/api/jobs/{id}/file` | the finished MP4 / MP3 |
| `GET` | `/api/status` | whether login cookies, ffmpeg and Deno are available, plus updater info |

Resolved links are cached for 30 minutes, because Instagram CDN URLs expire. The server only proxies
`*.cdninstagram.com` / `*.fbcdn.net` URLs.

## Tests

```bash
pip install pytest && python -m pytest
```

## Disclaimer

For personal use only. Download only content you own or have permission to use, and follow Instagram's Terms of Use.
