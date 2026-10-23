# Same Komodo 2.3.3 base audited for wrapper/config semantics, plus stdlib Python.
#
# Deliberately no USER: Periphery's job is to drive the host Docker socket and
# read the same-path runtime mount, and a container holding that socket is
# root-equivalent whatever uid it runs as. A non-root uid would still need
# socket access (the docker group), and would add file-permission failures
# without removing the host escape the socket already grants.
# The stock Periphery already has Docker and flock, but has no Python interpreter.
FROM ghcr.io/moghtech/komodo-periphery@sha256:bd79cf960ed054fe8e02384322303e462448679b1149dde48bbef151417255b1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 --version \
    && command -v flock \
    && command -v docker
