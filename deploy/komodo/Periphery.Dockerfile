# Same Komodo 2.3.3 base audited for wrapper/config semantics, plus stdlib Python.
# The stock Periphery already has Docker and flock, but has no Python interpreter.
FROM ghcr.io/moghtech/komodo-periphery@sha256:fa3f1a641a265216066676950d5eecdf506955a7bafe2f01996135ae93115cd5
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 --version \
    && command -v flock \
    && command -v docker
