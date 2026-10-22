# Same base + posture as deploy/stt/Dockerfile: a SEPARATE image with its own
# dependency set -- `voice-wake` is not a reuse of nora:latest, it is a
# different app (the stage-3 wake gate) that happens to live in the same repo.
FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# curl is needed for this Dockerfile's own HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.18

# uid/gid 1000 matches the host operator convention the top-level Dockerfile
# and deploy/stt both use.
RUN groupadd --gid 1000 wake \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/wake \
       --shell /usr/sbin/nologin wake

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY wakegate ./wakegate
# The wake model is BAKED IN: trained in the voice-lab (out of this repo) and
# copied here at integration time, so the image is self-contained and a
# rebuild reproduces the exact gated model (voice-lab data/modal-output-v3,
# openWakeWord 16x96 ONNX, int16 input).
COPY models ./models

# The boot self-test's clip. Shipped for the same reason the model is: the
# test asserts the wake path still wakes, and an image without the artefact
# it asserts against reports ERROR instead of PASS — which is exactly what
# happened the first time this was deployed without this line.
COPY selftest ./selftest

RUN chown -R wake:wake /app

USER wake

# 4599 = the wake-gate seam the sidecar connects OUT to (PUMP_HOST:PUMP_PORT,
# deploy/voice-sidecar/TRANSPORT.md); 4601 = /health. Neither is published --
# compose has NO `ports:` entry, so both stay on the private `nora` network.
EXPOSE 4599 4601

# No model download here, but per-user ONNX session setup gets headroom anyway.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:4601/health || exit 1

# --no-sync: the venv is already built at image build time; never reach out
# to PyPI at container runtime.
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "wakegate"]
