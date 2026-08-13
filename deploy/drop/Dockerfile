# python:3.12-slim matches Nora's own container base (../../Dockerfile) for
# consistency, but this is a SEPARATE image with its own dependency set --
# `drop` is not a reuse of nora:latest, it's a different app that happens to
# live in the same repo (docs/superpowers/specs/2026-08-12-nora-drop-design.md).
FROM python:3.12-slim

# curl is needed for this Dockerfile's own HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.18

# uid/gid 1000 matches the host operator convention the top-level Dockerfile
# already uses, so a future bind-mounted data/log directory needs no chmod
# dance or runtime `user:` override.
RUN groupadd --gid 1000 drop \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/drop \
       --shell /usr/sbin/nologin drop

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY server.py artifacts.py auth.py links.py slugs.py rendering.py bots.py hardening.py ./

RUN chown -R drop:drop /app

USER drop

# 8081 (internal, §5a) and 9102 (public, §6) -- EXPOSE is documentation only,
# the actual containment is docker-compose.yml having NO `ports:` entry for
# the internal one.
EXPOSE 8081 9102

# Checks the PUBLIC listener, matching this repo's `nora` service's own
# healthcheck pattern (curl against /health). The internal listener has no
# host-reachable port to check from outside the container by design (§5a) --
# docker-compose.yml's own healthcheck for this service checks the same
# endpoint from inside the `nora` network, and both listeners come up or fail
# together since they're one process (server.py's module docstring).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9102/health || exit 1

# --no-sync: the venv is already built at image build time; never reach out
# to PyPI at container runtime.
ENTRYPOINT ["uv", "run", "--no-sync", "python", "server.py"]
