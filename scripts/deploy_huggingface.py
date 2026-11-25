"""Deploy this app to a Hugging Face Space (Docker).

Creates the Space if it doesn't exist, uploads the app, and (optionally) stores
your cookies / proxy as private Space secrets.

    pip install huggingface_hub
    HF_TOKEN=hf_xxx python scripts/deploy_huggingface.py

Environment variables:
  HF_TOKEN       Hugging Face access token with *write* permission (required)
  HF_SPACE       Space name, or "user/name" (default: instasave)
  HF_PRIVATE     "1" to create the Space as private (default: public)
  COOKIES_TXT    optional cookies.txt contents -> Space secret COOKIES_TXT
  YT_COOKIES_TXT optional YouTube-only cookies -> Space secret YT_COOKIES_TXT
  YT_PROXY       optional proxy URL           -> Space secret YT_PROXY
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["Dockerfile", "start.sh", "requirements.txt", ".dockerignore"]
DIRS = ["app", "static"]
SECRETS = ["COOKIES_TXT", "YT_COOKIES_TXT", "YT_PROXY"]

FRONT_MATTER = """---
title: InstaSave
emoji: 📥
colorFrom: pink
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Download Instagram & YouTube videos, photos and MP3
---

"""


def build_bundle(out: Path) -> None:
    """Copy just what the Space needs into `out`, with a Space README header."""
    for f in FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, out / f)
    for d in DIRS:
        shutil.copytree(ROOT / d, out / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    (out / "README.md").write_text(FRONT_MATTER + readme, encoding="utf-8")


def space_url(repo_id: str) -> str:
    # https://<owner>-<name>.hf.space  (lowercase, '_' and '.' become '-')
    return "https://" + re.sub(r"[^a-z0-9-]", "-", repo_id.lower().replace("/", "-")) + ".hf.space"


def main() -> int:
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set. Create a token with *write* access at "
              "https://huggingface.co/settings/tokens", file=sys.stderr)
        return 1
    api = HfApi(token=token)
    space = os.environ.get("HF_SPACE", "").strip() or "instasave"
    repo_id = space if "/" in space else f"{api.whoami()['name']}/{space}"

    try:
        api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True,
                        private=os.environ.get("HF_PRIVATE") == "1")
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 402:
            print("Hugging Face refused: Docker Spaces need a PRO subscription (https://huggingface.co/pro).\n"
                  "Free accounts can only host static Spaces, which can't run this app's server.\n"
                  "See README -> Hosting for free alternatives.", file=sys.stderr)
            return 2
        raise
    print(f"Space: https://huggingface.co/spaces/{repo_id}")

    for key in SECRETS:
        if value := os.environ.get(key, "").strip():
            api.add_space_secret(repo_id, key, value)
            print(f"Set Space secret {key}")

    with tempfile.TemporaryDirectory() as tmp:
        build_bundle(Path(tmp))
        sha = os.environ.get("GITHUB_SHA", "")[:7]
        api.upload_folder(
            repo_id=repo_id, repo_type="space", folder_path=tmp,
            commit_message=f"Deploy {sha}".strip(),
            delete_patterns=["app/**", "static/**"],  # drop files that were removed from the repo
        )
    print(f"Uploaded. Hugging Face is now building the app (takes ~5 min): {space_url(repo_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
