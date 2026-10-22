# Same Komodo 2.3.3 base audited for wrapper/config semantics, plus stdlib Python.
#
# Deliberately no USER: Periphery's job is to drive the host Docker socket and
# read the same-path runtime mount, and a container holding that socket is
# root-equivalent whatever uid it runs as. A non-root uid would still need
# socket access (the docker group), and would add file-permission failures
# without removing the host escape the socket already grants.
# The stock Periphery already has Docker and flock, but has no Python interpreter.
FROM ghcr.io/moghtech/komodo-periphery@sha256:fa3f1a641a265216066676950d5eecdf506955a7bafe2f01996135ae93115cd5
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 --version \
    && command -v flock \
    && command -v docker
