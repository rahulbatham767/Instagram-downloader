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

## Run it on your computer with a public link (one command)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / Mac) or Docker Engine (Linux),
running. Then, in this folder:

```bash
docker compose up
```

That's all. The first run builds the image (about 5 minutes); later runs start in seconds. It starts:

- **the app** at http://localhost:8000 (this computer only);
- **a free Cloudflare tunnel** that gives it a public `https://<random-words>.trycloudflare.com` link, so your phone or
  friends can use it from anywhere. No Cloudflare account is needed. The link is printed in the terminal in a
  `PUBLIC LINK` box, and shown at the top of http://localhost:8000 with a **Copy** button.

Good to know:
- **The link works only while this computer is on** and Docker is running.
- **The link changes** each time the tunnel restarts (for example after a reboot). Open http://localhost:8000 to see the current one.
  A permanent link needs your own domain on Cloudflare (a "named tunnel"), which can be added later.
- **Run it in the background** with `docker compose up -d` so you can close the terminal. With Docker Desktop's
  *"Start Docker Desktop when you sign in"* setting on, the app and tunnel also come back by themselves after a reboot.
- **Anyone with the link can use it.** To limit it to you, create a file named `.env` next to `docker-compose.yml`
  with `ALLOWED_EMAILS=you@example.com` (comma-separate several). Cloudflare then asks visitors to confirm their email.
- **Cookies** (optional, for Instagram stories and YouTube bot checks): put `cookies.txt` in the `data` folder.
- Downloads use **your home internet connection**, so YouTube bot checks and Instagram login prompts are much rarer than on cloud servers.

## Run it on Android (Termux)

Install [Termux](https://f-droid.org/packages/com.termux/) from F-Droid (the Play Store version is outdated), then:

```bash
pkg install -y git
git clone -b ccr-65ae6f81-sx87z1 https://github.com/rahulbatham767/Instagram-downloader.git
cd Instagram-downloader
bash termux-start.sh
```

From then on, `bash termux-start.sh` is the only command: open http://localhost:8000 in your phone's browser.

- **The first run takes 10–20 minutes.** It installs Termux's own **ffmpeg** and **Node.js** (the pip builds of ffmpeg and
  Deno only work on regular Linux, not Android), plus clang and Rust, because some Python packages
  (like pydantic-core) have to be built from source on Android. Later runs start in seconds, and setup re-runs only when
  `requirements-core.txt` changes.
- **YouTube** uses Node.js instead of Deno, and the PO token server is installed and run with Node.js too. It runs
  without the optional `canvas` add-on, which can't be built on Android.
- **Public link:** `bash termux-start.sh --public` also starts a Cloudflare quick tunnel (`cloudflared` from Termux's
  packages) and prints the link. The same notes as the Docker setup apply: the link changes on every start, and anyone with it can use the app.
- **Keep it running with the screen off:** the script calls `termux-wake-lock`. Also turn off battery optimisation for Termux
  in Android settings, or Android may stop it in the background.
- Don't run `pip install -r requirements.txt` on Termux: it includes desktop-only packages (Deno, the pip ffmpeg) that can't install
  on Android. The script uses `requirements-core.txt`.

## Run without Docker (for development)

Requirements: Python 3.10+. Everything else comes from `requirements.txt`:

- **ffmpeg** (for MP3 and YouTube): a system-wide ffmpeg is used if you have one; otherwise the copy
  bundled in the `imageio-ffmpeg` package is set up automatically.
- **Deno** and **yt-dlp-ejs**, which yt-dlp needs to read YouTube.
- **The YouTube PO token server** is downloaded and started by the app on its first start (about a minute, once).
  It's kept in `~/.cache/instasave/` and runs on `127.0.0.1:4416` while the app runs. Set `AUTO_POT_SERVER=0` to turn this off.

```bash
pip install -r requirements.txt
python -m app --port 8000
```

Open http://localhost:8000, paste a link, and click **Download**.
Tip: `http://localhost:8000/?url=<instagram link>` fetches the link right away.

## Deploy to Hugging Face Spaces (needs Hugging Face PRO)

> **Note:** Since mid-2026, Hugging Face only allows Docker Spaces on **PRO** accounts. Free accounts get
> "402 Payment Required" because they can host static pages only, which can't run this app. The setup below works once
> your account has PRO.

A GitHub Action deploys the app to a [Hugging Face Space](https://huggingface.co/docs/hub/spaces-sdks-docker)
when you start it by hand. One-time setup:

1. Create a free account at https://huggingface.co and a token with **Write** access at
   https://huggingface.co/settings/tokens.
2. In this GitHub repo go to **Settings → Secrets and variables → Actions → New repository secret** and add
   `HF_TOKEN` with that token.
3. Optional:
   - secret `COOKIES_TXT`: the contents of your cookies.txt (Instagram and/or YouTube);
   - secret `YT_PROXY`: a residential proxy;
   - variable `HF_SPACE`: the Space name (default `instasave`).
4. Open **Actions → Deploy to Hugging Face → Run workflow** (each push only runs the tests).

The workflow runs the tests, creates the Space if needed, copies the optional secrets into it, and uploads the app.
Hugging Face then builds the Docker image (about 5 minutes). The app is at `https://<your-hf-username>-instasave.hf.space`.

You can also deploy from your computer: `pip install huggingface_hub && HF_TOKEN=hf_... python scripts/deploy_huggingface.py`.

Things to know about free Spaces:
- **Public by default:** anyone with the link can use the app, and therefore your cookies and proxy. Set
  `HF_PRIVATE=1` for the first deploy if only you should use it (but then the page needs a Hugging Face login).
- **Sleeps** after 48 hours without visitors and wakes on the next visit (about 1 minute). Files on disk reset on every restart,
  so the auto-updater re-installs updates shortly after each start.
- **Cloud IPs**: expect YouTube bot checks and Instagram login prompts more often than at home. The bundled
  PO token server helps; cookies (as a Space secret) help more.
- Hugging Face can restrict Spaces that break its [content policy](https://huggingface.co/content-guidelines)
  or other sites' terms. Keep it for personal use.

## Command line

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

**1. PO token provider (set up automatically, with or without Docker).**
The [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide) recommends a PO-token
provider plugin, and this app uses [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
(maintained by a yt-dlp maintainer). The plugin is in `requirements.txt` and kept updated by the auto-updater. It needs
its small token server running:

```bash
docker compose up -d --build        # the image already runs the token server
# without Docker, run just the token server next to `python -m app`:
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
