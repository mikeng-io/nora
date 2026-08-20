# python:3.12-slim matches Nora's own container base (../../Dockerfile) for
# consistency, but this is a SEPARATE image with its own dependency set --
# `stt` is not a reuse of nora:latest, it's a different app that happens to
# live in the same repo (docs/superpowers/specs/2026-08-21-nora-stt-infrastructure-design.md).
FROM python:3.12-slim

# curl is needed for this Dockerfile's own HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.18

# uid/gid 1000 matches the host operator convention the top-level Dockerfile
# already uses, so a future bind-mounted data/log directory needs no chmod
# dance or runtime `user:` override.
RUN groupadd --gid 1000 stt \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/stt \
       --shell /usr/sbin/nologin stt

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# funasr declares torch as a soft requirement (not in its metadata deps), so
# `uv sync` skips it — but AutoModel refuses to import without it. Install the
# CPU wheel explicitly (this host has no CUDA; the GPU index would pull a
# multi-GB build for nothing). Matches the voice-lab venv that measured
# SenseVoice on this exact host.
RUN uv pip install --python .venv/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY server.py ./

RUN chown -R stt:stt /app \
    # /home/stt/.cache/modelscope is the bind target for the modelscope volume;
    # pre-create + chown so the non-root stt user can write the model download
    # on first boot (an empty named volume mounts with the IMAGE's ownership —
    # without this, uv/FunASR fail to write the cache with EACCES).
    && mkdir -p /home/stt/.cache/modelscope \
    && chown -R stt:stt /home/stt/.cache

USER stt

# 8000 (internal only -- the endpoint Nora calls as `stt:8000`; docker-compose.yml
# deliberately has NO `ports:` entry so the transcription surface is reachable
# only on the private `nora` network, never published to the host).
EXPOSE 8000

# The model weights are downloaded at first boot from ModelScope
# (~/.cache/modelscope inside the container). The first healthcheck may take
# longer than the default start_period while the model downloads -- compose
# overrides start_period for this reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# --no-sync: the venv is already built at image build time; never reach out
# to PyPI at container runtime.
ENTRYPOINT ["uv", "run", "--no-sync", "python", "server.py"]
CMD ["--model", "sensevoice", "--device", "cpu", "--host", "0.0.0.0", "--port", "8000"]
