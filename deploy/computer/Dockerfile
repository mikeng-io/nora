# Nora's computer (design/sandbox-runtime §5): the image the sbx microVM boots.
# Built on the host, loaded into sbx with `nora-computer build`, never run by
# the host's own Docker. It is a working machine, not a service image: the
# toolchain is what a coding agent expects to find, and computerd is the only
# thing that answers Nora.
#
# sbx's `shell` agent does NOT run this image's ENTRYPOINT (spike round 2), so
# none is set: `nora-computer ensure` starts computerd with `sbx exec`.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates coreutils curl file git jq less procps sqlite3 unzip zip \
        python3 python3-venv python3-pip \
        nodejs npm \
        ffmpeg imagemagick pandoc poppler-utils \
        fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# uv for per-project environments (a project's .venv + lockfile persist under
# /workspace; the system Python does not).
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# computerd in its own venv, so a project's `pip install` can never break the
# thing that answers Nora.
COPY computerd.py /opt/computerd/computerd.py
RUN python3 -m venv /opt/computerd/venv \
    && /opt/computerd/venv/bin/pip install --no-cache-dir "aiohttp>=3.9,<4"

# The working user. /workspace holds one directory per room scope.
RUN useradd --create-home --uid 1500 --shell /bin/bash computer \
    && mkdir -p /workspace && chown computer:computer /workspace
USER computer
WORKDIR /workspace
