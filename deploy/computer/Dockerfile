# Nora's computer (design/sandbox-runtime §5, ADR 0019): the image the sbx
# microVM boots. Built on the host, loaded into sbx with `nora-computer build`,
# never run by the host's own Docker. It is a working machine, not a service
# image: the toolchain is what a coding agent expects to find, and computerd
# is the only thing that answers Nora.
#
# sbx's `shell` agent does NOT run this image's ENTRYPOINT (spike round 2), so
# none is set: `nora-computer ensure` starts computerd, as root, with `sbx exec`.
#
# Ubuntu and uv are pinned by digest, computerd's Python dependencies by
# hash (requirements.txt, from `uv pip compile --generate-hashes`). Bump those
# deliberately with a rebuild. Node is the exception: its Node 24 / trixie
# tag floats patch updates, refreshed by `nora-computer build` with --pull.
FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS node

FROM ubuntu@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        bash ca-certificates coreutils curl file git jq less procps sqlite3 tmux unzip zip \
        python3 python3-venv python3-pip \
        libstdc++6 libatomic1 \
        ffmpeg imagemagick pandoc poppler-utils \
        fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Official Node binaries and npm, with their runtime libraries installed above.
# Keep Ubuntu's distro Python for computerd and the system tools.
COPY --from=node /usr/local/ /usr/local/

# uv for per-project environments (a project's .venv and lockfile persist
# under /workspace; the system Python does not). 0.12.21, by digest.
COPY --from=ghcr.io/astral-sh/uv@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /uvx /usr/local/bin/

# computerd in its own venv, so a project's `pip install` can never break the
# thing that answers Nora. Root-owned: commands run as their room's own
# user and cannot replace it.
COPY requirements.txt computerd.py /opt/computerd/
RUN python3 -m venv /opt/computerd/venv \
    && /opt/computerd/venv/bin/pip install --no-cache-dir --require-hashes \
        -r /opt/computerd/requirements.txt

# nora-render (design/sandbox-runtime §5, S3a): Smith's image renderers as
# one CLI, `nora-render chart|mermaid IN OUT` (render/nora_render.py),
# standard-library Python on the image's python3. Each makes a picture, never
# a page: documents are the document path's (ADR 0020). librsvg's
# rsvg-convert turns a chart's SVG into a PNG. mermaid-cli comes by `npm ci`
# from render/package-lock.json, whose integrity hashes pin every package;
# its postinstall fetches the Chromium it draws with, at build time, into
# /opt/nora-render/puppeteer (the libraries below are what that Chromium
# links against). Nothing here reaches the network at run time.
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libcairo2 libcups2t64 \
        libdrm2 libgbm1 libnss3 libpango-1.0-0 libxcomposite1 libxdamage1 \
        libxfixes3 libxkbcommon0 libxrandr2 librsvg2-bin \
    && rm -rf /var/lib/apt/lists/*
COPY render/package.json render/package-lock.json /opt/nora-render/
RUN cd /opt/nora-render \
    && PUPPETEER_CACHE_DIR=/opt/nora-render/puppeteer npm ci --omit=dev --no-audit --no-fund \
    && chmod -R a+rX /opt/nora-render
COPY render/nora_render.py render/nora-render render/mmdc /opt/nora-render/
RUN ln -s /opt/nora-render/nora-render /usr/local/bin/nora-render \
    && ln -s /opt/nora-render/mmdc /usr/local/bin/mmdc

# The browser (design/browser-runtime, ADR 0022, B1): Google Chrome stable,
# headed on Xvfb, one per room, started by computerd over
# --remote-debugging-pipe. Pinned by version and sha256; bump both together,
# deliberately, since a stale Chrome is itself a bot signal (design 00 §3).
ARG CHROME_VERSION=155.0.8059.39-1
ARG CHROME_SHA256=c58aa0f2cd66179c9f050e062c882d27aa9b9f8c2b7c73fee3498560b5ed0b38
RUN curl -fsSLo /tmp/chrome.deb \
        "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_amd64.deb" \
    && echo "${CHROME_SHA256}  /tmp/chrome.deb" | sha256sum -c - \
    && apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends /tmp/chrome.deb xvfb \
    && rm -rf /var/lib/apt/lists/* /tmp/chrome.deb \
    && rm -f /etc/apt/sources.list.d/google-chrome.list \
    && mkdir -p /tmp/.X11-unix && chown root:root /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

# The operator's user, for an interactive `sbx exec`. /workspace is root-owned
# so a command cannot swap a room's directory for a link; computerd creates
# each room's directory 0700, owned by that room's own system user
# (`c-<12 hex>`, made on first use), and runs the room's commands as it.
RUN useradd --create-home --uid 1500 --shell /bin/bash computer \
    && mkdir -p /workspace && chmod 755 /workspace
USER computer
WORKDIR /workspace
