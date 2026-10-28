# The portal, built as a Next.js standalone server: the build stage has the
# full toolchain, the runtime stage only the traced server, its static
# assets and public/. `docker build --network=host` on the deploy host
# (AGENTS.md, operational constraint 1).
FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# /downloads shells out to the yt-dlp binary (src/server/downloads/runner.ts);
# ffmpeg merges best-video+best-audio into the mp4 --merge-output-format asks
# for. The binary is the pinned, checksum-verified release — the same version
# floor pyproject.toml declares for Nora's own yt-dlp use (>=2026.8.19).
ARG YTDLP_VERSION=2026.08.19
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
    && arch="$(uname -m)" \
    && case "$arch" in x86_64) bin=yt-dlp_linux ;; aarch64) bin=yt-dlp_linux_aarch64 ;; *) echo "unsupported arch $arch" >&2; exit 1 ;; esac \
    && curl -fsSL -o /usr/local/bin/yt-dlp "https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/${bin}" \
    && curl -fsSL -o /tmp/SHA2-256SUMS "https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/SHA2-256SUMS" \
    && grep "  ${bin}$" /tmp/SHA2-256SUMS | sed "s/  ${bin}$/  yt-dlp/" | (cd /usr/local/bin && sha256sum -c -) \
    && chmod 755 /usr/local/bin/yt-dlp \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/SHA2-256SUMS \
    && mkdir -p /data/downloads && chown node:node /data/downloads
ENV YTDLP_PATH=/usr/local/bin/yt-dlp NORA_DOWNLOADS_DIR=/data/downloads NORA_DOWNLOADS_TTL_HOURS=24
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
