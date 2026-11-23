# InstaSave — Instagram Downloader

Paste an Instagram link and download what's in it:

| Content | Link example | Output |
|---|---|---|
| Reels / videos | `instagram.com/reel/<code>/`, `/tv/<code>/` | MP4 |
| Photos & carousels | `instagram.com/p/<code>/` | JPG / MP4 per slide |
| Stories (one item or all of a user's) | `instagram.com/stories/<user>/<id>/`, `/stories/<user>/` | JPG / MP4 |
| Audio | any video above | MP3 (via ffmpeg) |

It's a FastAPI backend with a single-page web UI. It uses
[instaloader](https://instaloader.github.io/) first and falls back to [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Run it

Requirements: Python 3.10+, and `ffmpeg` on your PATH for MP3 audio.

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000, paste a link, and click **Download**.
Tip: `http://localhost:8000/?url=<instagram link>` fetches the link right away.

With Docker:

```bash
docker build -t instasave .
docker run -p 8000:8000 -v "$PWD/cookies.txt:/app/cookies.txt:ro" instasave
```

### Command line

```bash
python -m app.cli https://www.instagram.com/reel/XXXX/           # save the video
python -m app.cli https://www.instagram.com/reel/XXXX/ --audio   # save as MP3
python -m app.cli https://www.instagram.com/p/XXXX/ -o my_folder # every carousel item
```

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
| `GET` | `/api/status` | whether login cookies and ffmpeg are available |

Resolved links are cached for 30 minutes, because Instagram CDN URLs expire. The server only proxies
`*.cdninstagram.com` / `*.fbcdn.net` URLs.

## Tests

```bash
pip install pytest && python -m pytest
```

## Disclaimer

For personal use only. Download only content you own or have permission to use, and follow Instagram's Terms of Use.
