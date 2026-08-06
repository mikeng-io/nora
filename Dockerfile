# python:3.12-slim matches the host's default interpreter (requires-python >=3.11 in
# pyproject.toml; the dev .venv happened to be built with 3.13, but 3.12 is what the
# host actually runs, so that's what we validate the container against).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.18

# uid/gid 1000 matches the host operator (norty) so the bind-mounted ./data directory
# is writable without a runtime `user:` override or a chmod dance.
RUN groupadd --gid 1000 nora \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/nora \
       --shell /usr/sbin/nologin nora

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY nora/ ./nora/
COPY evals/ ./evals/
COPY config.example.toml identity.example.toml alembic.ini ./

RUN uv sync --frozen --no-dev

RUN mkdir -p /app/data && chown -R nora:nora /app

USER nora

EXPOSE 9100

# Real config.toml / identity.toml / policy.toml / doctrine files are bind-mounted at runtime by
# docker-compose.yml — never baked into the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:9100/health || exit 1

# --no-sync: the venv is already built; never let the container reach out to PyPI at
# runtime (also matters if egress is ever locked down for the container's uid later).
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "nora"]
CMD ["--config", "config.toml"]
