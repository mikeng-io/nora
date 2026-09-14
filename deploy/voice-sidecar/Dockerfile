# node:22-slim matches the Node 22 the spike was verified with on this host
# (design/voice-channel/05-sidecar-receive.md). This is a SEPARATE image with
# its own dependency set — the same "separate app that lives in this repo"
# shape as deploy/stt/.
FROM node:22-slim

# curl is needed for this Dockerfile's own HEALTHCHECK below. The runtime
# deps (@discordjs/opus, @snazzah/davey) ship prebuilt linux-x64 glibc
# binaries, so no toolchain is installed; if a future bump drops the
# prebuilds, npm ci will need python3/make/g++ here instead.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uid/gid 1000 matches the host operator convention the top-level Dockerfile
# and deploy/stt/Dockerfile already use.
RUN groupadd --gid 1000 voice \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/voice \
       --shell /usr/sbin/nologin voice

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY src/ ./src/
COPY spike.mjs consume.mjs smoke_test.mjs ./

RUN chown -R voice:voice /app

USER voice

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
