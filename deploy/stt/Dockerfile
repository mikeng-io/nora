# python:3.12-slim-trixie matches Nora's own container base (../../Dockerfile) for
# consistency, but this is a SEPARATE image with its own dependency set --
# `stt` is not a reuse of nora:latest, it's a different app that happens to
# live in the same repo (docs/superpowers/specs/2026-08-21-nora-stt-infrastructure-design.md).
FROM python:3.12-slim-trixie

# curl is needed for this Dockerfile's own HEALTHCHECK below; ffmpeg gives
# soundfile a universal decoder for signal voice notes (opus/ogg works without
# it, but audio/mp4 — a common Signal voice-note container — fails to open).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg \
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
# torch/torchaudio are real project dependencies now, pinned to the CPU wheel
# index in pyproject.toml's [tool.uv.sources] — `uv sync` installs the ~200MB
# CPU build directly (this host has no CUDA; the GPU index would pull a
# multi-GB build for nothing). Matches the voice-lab venv that measured both
# engines on this exact host.
RUN uv sync --frozen --no-dev

COPY server.py config_toml.py ./

RUN chown -R stt:stt /app \
    # ~/.cache/modelscope is the bind target for the SenseVoice weights volume
    # and ~/.cache/huggingface for the whisper (default model) weights volume;
    # pre-create + chown so the non-root stt user can write the model downloads
    # on first boot (an empty named volume mounts with the IMAGE's ownership —
    # without this, the download fails with EACCES).
    && mkdir -p /home/stt/.cache/modelscope /home/stt/.cache/huggingface \
    && chown -R stt:stt /home/stt/.cache

USER stt

# 8000 (internal only -- the endpoint Nora calls as `stt:8000`; docker-compose.yml
# deliberately has NO `ports:` entry so the transcription surface is reachable
# only on the private `nora` network, never published to the host).
EXPOSE 8000

# The DEFAULT model weights (~3.2GB fp32) download at first boot from Hugging
# Face into ~/.cache/huggingface; the SenseVoice backup (~936MB) downloads from
# ModelScope into ~/.cache/modelscope on first use. The first healthcheck may
# take longer than the default start_period while the model downloads --
# compose overrides start_period for this reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# --no-sync: the venv is already built at image build time; never reach out
# to PyPI at container runtime.
ENTRYPOINT ["uv", "run", "--no-sync", "python", "server.py"]
# Default engine switched to sensevoice (2026-09-16): warm CPU latency
# measured 0.3s vs 5.1s for the whisper transformer on a 2s clip — the
# wake→reply path's 30-40s delay was dominated by whisper fp32 CPU
# inference. The whisper entry stays available (`--model
# noise-detection` / form param) for A/B and the INT8 experiment.
CMD ["--model", "sensevoice", "--device", "cpu", "--host", "0.0.0.0", "--port", "8000"]
