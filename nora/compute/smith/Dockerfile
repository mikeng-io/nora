FROM python:3.12-slim

# Same reason as nora's/signal-cli's own Dockerfiles (CLAUDE.md operational
# constraint 1): must be built with --network=host on this host, or
# BuildKit's default bridge can't resolve deb.debian.org.
RUN pip install --no-cache-dir uv

# mermaid-cli (mmdc) needs Node.js, AND it renders via a headless Chromium
# under Puppeteer -- a bare `nodejs`/`npm` install is not enough. Without
# Chromium and its own shared-library dependency chain, `mmdc` fails at
# RUNTIME (not build time) with missing-library errors (a well-documented
# mermaid-cli Docker pitfall: `libgbm.so.1` and friends). An earlier draft
# of this Dockerfile only installed nodejs/npm and passed its own `docker
# build` check, because that check never actually invokes `mmdc` against a
# real diagram -- only Task 3's unit tests exercise render_mermaid's LOGIC,
# and those mock subprocess.run entirely, so nothing there would have
# caught this either.
#
# PUPPETEER_SKIP_DOWNLOAD=true is set BEFORE `npm install` -- confirmed as
# the correct, current env var name (not the older
# PUPPETEER_SKIP_CHROMIUM_DOWNLOAD one might expect from older docs) by
# actually running this build: Debian trixie's `apt` Node.js is v20.19.2,
# below Puppeteer 25.x's declared `>=22.12.0` (a non-fatal EBADENGINE
# warning), and letting `npm install` try its own Chromium download here
# FAILS the build outright ("no zip archiver is available. Install
# `unzip`") since this slim image has no `unzip`. Skipping that download
# is not just a workaround -- it is also strictly better: it avoids a
# 200MB+ download of a browser build the image would never point at
# anyway, since PUPPETEER_EXECUTABLE_PATH below always routes `mmdc` to
# the apt-installed system chromium instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm \
        chromium \
        fonts-liberation libnss3 libatk-bridge2.0-0 libatk1.0-0 \
        libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && PUPPETEER_SKIP_DOWNLOAD=true npm install -g @mermaid-js/mermaid-cli \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# `mmdc` launches Puppeteer at RUNTIME (not just at npm-install time), and
# without a downloaded Chromium of its own (see PUPPETEER_SKIP_DOWNLOAD
# above), Puppeteer needs to be told where to find a browser -- this points
# it at the apt-installed system chromium instead of trying to run a copy
# it never downloaded.
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system --no-cache .

COPY agent.py doctrine.md ./
COPY skills/ skills/

WORKDIR /workspace
ENTRYPOINT ["python3", "/app/agent.py"]
