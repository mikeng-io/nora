# bridge-projection — the standalone admission-projection publisher
# (design/platform-bridges §02). Writes /bridge-projection/{platform}.json
# from the canonical config/policy/identity files, atomically, on the existing
# 3s reload cadence — and keeps publishing while the app process is down:
# it reads files, never a live app.
#
# Same base + uv bootstrap + uid 1000 convention as Dockerfile and
# deploy/stt/Dockerfile.
FROM python:3.15-slim-trixie@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0

# curl: the compose healthcheck curls /health when NORA_BRIDGE_PROJECTION_PORT
# is set.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/

RUN groupadd --gid 1000 nora \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/nora \
       --shell /usr/sbin/nologin nora

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY nora/ ./nora/
COPY deploy/bridge-projection/entry.py /app/entry.py
RUN uv sync --frozen --no-dev \
    && mkdir -p /bridge-projection && chown nora:nora /bridge-projection

USER nora

# The publisher carries no secrets and no credential mounts: config.toml is
# mounted read-only (require_secrets=False — secret.toml is never opened), the
# projection volume is its only write. account_key arrives via
# NORA_BRIDGE_ACCOUNT_KEY; the bearer token envs only matter if --self-url is
# pointed at a bridge's /v1/self. entry.py is the composition root: it wires
# the real config loader into nora.platform.bridge.projection, which stays
# layer-pure (no config/policy/engagement imports from nora.platform).
ENTRYPOINT ["/app/.venv/bin/python", "/app/entry.py"]
