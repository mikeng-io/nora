# node:22-slim matches the Node 22 the spike was verified with on this host
# (design/voice-channel/05-sidecar-receive.md). This is a SEPARATE image with
# its own dependency set — the same "separate app that lives in this repo"
# shape as deploy/stt/.
# Pin node 22.22.3-slim (NOT the floating node:22-slim): the DAVE spike and
# `npm ci` were verified on 22.22.3 with @discordjs/opus@0.10.0's prebuilt
# binaries; floating node:22 currently resolves to 22.23.x whose node-v127
# ABI has no opus prebuild, and the image has no toolchain to compile the
# fallback (node-gyp needs python3/make/g++ — not installed by design).
FROM node:22.22.3-slim

# curl is needed for this Dockerfile's own HEALTHCHECK below. The runtime
# deps (@discordjs/opus, @snazzah/davey) ship prebuilt linux-x64 glibc
# binaries, but @discordjs/opus@0.10.0 only provides a glibc-2.43 prebuild —
# Debian bookworm (glibc 2.36) misses it and falls back to source compile,
# so python3/make/g++ are needed for node-pre-gyp despite the prebuilds
# (this is the "future bump drops the prebuilds" case the README foresaw).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

# node:22-slim already ships user `node` (uid/gid 1000 — the SAME uid the
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
