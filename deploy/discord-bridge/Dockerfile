# discord-bridge — durable Discord boundary: nora package + the bridge entry,
# on the repo's own image approach (../../Dockerfile,
# deploy/bridge-projection/Dockerfile): pinned python:3.12-slim-trixie, the
# pinned uv binary, and a FROZEN `uv sync`.
FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# curl runs the compose healthcheck; ffmpeg is the playback transcoder the
# owned music path needs (same package deploy/stt and the app image install).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/

RUN groupadd --gid 1000 nora \
 && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/nora \
    --shell /usr/sbin/nologin nora

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY nora/ ./nora/
COPY deploy/discord-bridge/entry.py /app/entry.py
RUN uv sync --frozen --no-dev \
 && mkdir -p /data/bridge && chown -R nora:nora /data /app

USER nora

ENV NORA_BRIDGE_JOURNAL=/data/bridge/journal/discord.jsonl

EXPOSE 18093

ENTRYPOINT ["/app/.venv/bin/python", "/app/entry.py"]
