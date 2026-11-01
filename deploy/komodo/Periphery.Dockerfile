# Same Komodo 2.3.3 base audited for wrapper/config semantics, plus stdlib Python
# and updated system CAs for Periphery's HTTPS Git fetches.
#
# Deliberately no USER: Periphery's job is to drive the host Docker socket and
# read the same-path runtime mount, and a container holding that socket is
# root-equivalent whatever uid it runs as. A non-root uid would still need
# socket access (the docker group), and would add file-permission failures
# without removing the host escape the socket already grants.
# The stock Periphery already has Docker and flock, but has no Python interpreter.
# Keep the release tag: the untagged digest update selected an older bullseye image.
FROM ghcr.io/moghtech/komodo-periphery:2.3.4@sha256:fb4b52961fcce7a31e3776930e422967b6640f653de876f59f26dea243e29beb
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 --version \
    && python3 -c "import tomllib" \
    && command -v flock \
    && command -v docker
