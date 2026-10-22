# Separate audio-controller image with its own dependency set, on the same
# Node 24 / Debian trixie family as the other Node services.
FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea

# curl runs the healthcheck; ffmpeg transcodes playback. Native audio
# packages may lack a prebuild for the selected Node ABI, architecture or
# glibc, so retain python3/make/g++ for @discordjs/opus's source fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl python3 make g++ ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# node:24-trixie-slim already ships user `node` (uid/gid 1000 — the SAME uid the
# top-level Dockerfile and deploy/stt use) AND a pre-existing system group
# named `voice` (gid 22). A groupadd/useradd here would collide on both the
# group name and uid 1000, so the container simply runs as the base image's
# own `node` user — same uid convention, zero extra accounts.
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY src/ ./src/
COPY spike.mjs consume.mjs smoke_test.mjs ./

RUN chown -R node:node /app

# Durable desired-state dir (design/voice-channel/07 §12.1): the compose
# volume mounts /var/lib/audio-controller and the runtime user is `node` —
# create + chown it here or every desired-state write fails with EACCES.
RUN mkdir -p /var/lib/audio-controller && chown node:node /var/lib/audio-controller

USER node

# 4600 is the health endpoint, internal only — docker-compose.yml has NO
# `ports:` entry, so the surface is reachable only on the private `nora`
# network (same posture as stt:8000).
EXPOSE 4600

# /health is process liveness served by src/service.mjs itself; it answers
# even while the service idles without a token (healthy-but-idle — see
# README.md "Health semantics"). $PORT matches the service's own env.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-4600}/health" || exit 1

# Nothing about the build or this command waits on DISCORD_BOT_TOKEN: the
# service starts, serves /health, and only joins Discord when the env says
# where (src/service.mjs).
ENTRYPOINT ["node", "src/service.mjs"]
