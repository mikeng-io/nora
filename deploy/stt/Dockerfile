FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 AS llama-build
RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ ninja-build libvulkan-dev glslc spirv-headers glslang-dev && rm -rf /var/lib/apt/lists/*
RUN git init /src && cd /src && git remote add origin https://github.com/ggml-org/llama.cpp.git && git fetch --depth=1 origin 78ffbaa18533c2ed51eab2ef5ae0702330efc3e9 && git checkout FETCH_HEAD
RUN set -e; for device in cpu vulkan; do \
      if [ "$device" = vulkan ]; then vk=ON; else vk=OFF; fi; \
      cmake -S /src -B /build/$device -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=OFF -DGGML_VULKAN=$vk -DGGML_CUDA=OFF -DLLAMA_CURL=OFF \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
        -DCMAKE_INSTALL_RPATH='$ORIGIN' && \
      cmake --build /build/$device --target llama-server -j 4 && \
      mkdir -p /out/$device && cp -a /build/$device/bin/llama-server /build/$device/bin/*.so* /out/$device/; \
    done

# python:3.12-slim-trixie matches Nora's own container base (../../Dockerfile) for
# consistency, but this is a SEPARATE image with its own dependency set --
# `stt` is not a reuse of nora:latest, it's a different app that happens to
# live in the same repo (docs/superpowers/specs/2026-08-21-nora-stt-infrastructure-design.md).
FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# curl is needed for this Dockerfile's own HEALTHCHECK below; ffmpeg gives
# soundfile a universal decoder for signal voice notes (opus/ogg works without
# it, but audio/mp4 — a common Signal voice-note container — fails to open).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg libvulkan1 mesa-vulkan-drivers vulkan-tools \
    && rm -rf /var/lib/apt/lists/*

# Patched embedded TLS roots; avoid the base image pip network bootstrap.
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/

# uid/gid 1000 matches the host operator convention the top-level Dockerfile
# already uses, so a future bind-mounted data/log directory needs no chmod
# dance or runtime `user:` override.
RUN groupadd --gid 1000 stt \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/stt \
       --shell /usr/sbin/nologin stt

COPY --from=llama-build /out/ /opt/llama/

WORKDIR /app

COPY pyproject.toml uv.lock ./
# torch/torchaudio are real project dependencies now, pinned to the CPU wheel
# index in pyproject.toml's [tool.uv.sources] — `uv sync` installs the ~200MB
# CPU build directly (this host has no CUDA; the GPU index would pull a
# multi-GB build for nothing). Matches the voice-lab venv that measured both
# engines on this exact host.
RUN uv sync --frozen --no-dev

COPY --chown=stt:stt server.py config_toml.py qwen.py ./

RUN chown stt:stt /app /app/.venv \
    && if [ ! -f /app/.venv/.lock ]; then touch /app/.venv/.lock; fi \
    && chown stt:stt /app/.venv/.lock \
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
# Config selects preload; missing config retains the SenseVoice fallback.
CMD ["--device", "cpu", "--host", "0.0.0.0", "--port", "8000"]
