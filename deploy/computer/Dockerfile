# Nora's computer (design/sandbox-runtime §5, ADR 0019): the image the sbx
# microVM boots. Built on the host, loaded into sbx with `nora-computer build`,
# never run by the host's own Docker. It is a working machine, not a service
# image: the toolchain is what a coding agent expects to find, and computerd
# is the only thing that answers Nora.
#
# sbx's `shell` agent does NOT run this image's ENTRYPOINT (spike round 2), so
# none is set: `nora-computer ensure` starts computerd, as root, with `sbx exec`.
#
# Everything fetched is pinned: the base image and uv by digest, computerd's
# Python dependencies by hash (requirements.txt, from `uv pip compile
# --generate-hashes`). Bump them deliberately, in one commit, with a rebuild.
FROM ubuntu@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates coreutils curl file git jq less procps sqlite3 tmux unzip zip \
        python3 python3-venv python3-pip \
        nodejs npm \
        ffmpeg imagemagick pandoc poppler-utils \
        fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

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

# The operator's user, for an interactive `sbx exec`. /workspace is root-owned
# so a command cannot swap a room's directory for a link; computerd creates
# each room's directory 0700, owned by that room's own system user
# (`c-<12 hex>`, made on first use), and runs the room's commands as it.
RUN useradd --create-home --uid 1500 --shell /bin/bash computer \
    && mkdir -p /workspace && chmod 755 /workspace
USER computer
WORKDIR /workspace
