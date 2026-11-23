# Runs the downloader + the YouTube PO token server in one container.
# Works locally (docker compose up), and on Hugging Face Spaces (port 7860, user 1000).
ARG BGUTIL_VERSION=2.0.2

# ---- build the bgutil PO token server's dependencies -------------------------
FROM node:22-bookworm-slim AS pot
ARG BGUTIL_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${BGUTIL_VERSION}" \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /pot
WORKDIR /pot/server
RUN npm ci --omit=dev --no-audit --no-fund

# ---- app image ---------------------------------------------------------------
FROM python:3.12-slim-bookworm
ARG BGUTIL_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run containers as UID 1000. Everything (including the
# virtualenv) is owned by that user so the auto-updater can pip install.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DENO_DIR=/home/user/.cache/deno \
    DENO_NO_UPDATE_CHECK=1 \
    DENO_NO_PROMPT=1
RUN python -m venv /home/user/venv
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
# plugin version must match the bundled token server, so it's pinned here
RUN pip install --no-cache-dir -r requirements.txt "bgutil-ytdlp-pot-provider==${BGUTIL_VERSION}"

COPY --from=pot --chown=user /pot/server/package.json /pot/server/deno.lock /pot/server/tsconfig.json /home/user/pot-provider/
COPY --from=pot --chown=user /pot/server/src /home/user/pot-provider/src
COPY --from=pot --chown=user /pot/server/types /home/user/pot-provider/types
COPY --from=pot --chown=user /pot/server/node_modules /home/user/pot-provider/node_modules
RUN cd /home/user/pot-provider && deno cache --frozen src/main.ts

COPY --chown=user app ./app
COPY --chown=user static ./static
COPY --chown=user --chmod=755 start.sh .

ENV PORT=7860 \
    YT_POT_PROVIDER_URL=http://127.0.0.1:4416 \
    IG_COOKIES_FILE=/home/user/app/data/cookies.txt \
    AUTO_UPDATE_SKIP=bgutil-ytdlp-pot-provider
EXPOSE 7860
CMD ["./start.sh"]
