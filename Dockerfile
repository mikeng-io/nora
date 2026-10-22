# python:3.12-slim-trixie matches the host's default interpreter (requires-python >=3.11 in
# pyproject.toml; the dev .venv happened to be built with 3.13, but 3.12 is what the
# host actually runs, so that's what we validate the container against).
FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# ffmpeg is the Discord Music design's playback transcoder (design/discord-music/
# 01-playback-and-source.md) -- discord.py's FFmpegPCMAudio shells out to whatever
# `ffmpeg` it finds on PATH, it doesn't bundle one. Same package deploy/stt/Dockerfile
# already installs it for, for the same underlying reason (a universal decoder for
# audio this process didn't produce itself).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.18

# uid/gid 1000 matches the reference host operator so the bind-mounted ./data directory
# is writable without a runtime `user:` override or a chmod dance.
RUN groupadd --gid 1000 nora \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/nora \
       --shell /usr/sbin/nologin nora

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY nora/ ./nora/
COPY evals/ ./evals/
COPY scripts/check_version_stamp.py scripts/standing_edit.py ./scripts/
COPY config.example.toml identity.example.toml alembic.ini ./

# The version Nora reports is baked HERE and nowhere else. `nora/version.json`
# is generated on the host by scripts/gen_version_manifest.py (the container has
# no git checkout, .dockerignore excludes .git/, and github.com may be
# unreachable — so a runtime lookup would describe the repository rather than
# this artifact) and arrives with the `COPY nora/` above, in the same filesystem
# snapshot as the code it describes.
#
# The gate below refuses the build when a CLAIMED version and the baked stamp
# disagree — either way round: a mismatch, or a claim with no manifest to read.
# That is what makes "the reported version cannot disagree with the running
# code" a property rather than a hope. An UNSTAMPED build (no NORA_VERSION) is
# allowed and drops any stale manifest, because an image that reports nothing
# cannot disagree with anything — refusing it only broke `docker compose build`
# and the plain `docker build` in README. Build with
# `scripts/build-image.sh V3`, which stamps and passes the same value.
ARG NORA_VERSION=""
RUN python scripts/check_version_stamp.py "$NORA_VERSION"

# `--extra sentry` so the deployment image CAN report errors; whether it DOES
# is `[obs.errors] enabled` plus a DSN in the environment, both off by default.
# Installing it unconditionally and leaving it inert is the right way round: an
# operator turning Sentry on should not need a rebuild, and `nora/obs/errors.py`
# is a no-op without a DSN.
RUN uv sync --frozen --no-dev --extra sentry

RUN mkdir -p /app/data && chown -R nora:nora /app

# WHICH COMMIT this image was built from, and when, for every build and not
# only a stamped release: the deploy revision each process start records
# (`nora.app.revisions`, design/cognition-loop/70-migration.md §5) reads
# them, so "what code was running at T" is answerable on an ordinary,
# unversioned deploy too. `.git` is not in the build context
# (`.dockerignore`), so the sha arrives as a build argument: the build
# scripts and the compose build nora-deploy runs pass `git rev-parse HEAD`.
# Empty when a bare `docker build` passes nothing, and the deploy row then
# says so. Declared this late so it busts no dependency layer above.
ARG NORA_GIT_SHA=""
ARG NORA_BUILT_AT=""
ENV NORA_GIT_SHA=$NORA_GIT_SHA NORA_BUILT_AT=$NORA_BUILT_AT

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
