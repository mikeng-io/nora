# signal-bridge — the durable Signal boundary (design/platform-bridges).
#
# The signal-cli inside is the SAME verified distribution deploy/signal-cli
# builds: signed upstream tarball, signature re-verified in this build, Java 25
# JRE, unchanged binary and receive mode. What changes is who supervises it:
# wrapper.py preflights the capture journal before launch, captures SSE,
# normalizes, journals and dedupe-enqueues, and owns the /v1 actions API.
#
# The Python side follows the repo's own image approach (../../Dockerfile,
# deploy/bridge-projection/Dockerfile): the pinned uv binary installs the
# project from the FROZEN uv.lock into a .venv built inside THIS image — no
# cross-distro package copies, no unpinned installs.

FROM eclipse-temurin:25-jre-noble@sha256:3f2223fe04b750aa5a8179e29b5c9a1ddf7665fc9bb08bfe11ad01d143bc13a5 AS verified

ARG SIGNAL_CLI_VERSION=0.14.7
ARG SIGNAL_CLI_KEY=FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570

RUN apt-get update \
 && apt-get install -y --no-install-recommends gnupg \
 && rm -rf /var/lib/apt/lists/*

COPY deploy/signal-cli/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz     /tmp/
COPY deploy/signal-cli/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz.asc /tmp/
RUN gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "${SIGNAL_CLI_KEY}" \
 && gpg --batch --verify /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz.asc \
                         /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz \
 && tar xzf /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz -C /opt

# Only the verified distribution crosses into runtime: no archive or build GPG.
FROM eclipse-temurin:25-jre-noble@sha256:3f2223fe04b750aa5a8179e29b5c9a1ddf7665fc9bb08bfe11ad01d143bc13a5
ARG SIGNAL_CLI_VERSION=0.14.7

# python3 (noble's 3.12) is the interpreter `uv sync` builds the venv on, so
# the runtime interpreter and the installed packages are one image — a venv or
# site-packages copied from another base would carry that base's libpython.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl python3 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/

COPY --from=verified /opt/signal-cli-${SIGNAL_CLI_VERSION} /opt/signal-cli-${SIGNAL_CLI_VERSION}
RUN ln -s /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli /usr/local/bin/signal-cli

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY nora/ ./nora/
COPY deploy/signal-bridge/wrapper.py /app/wrapper.py
RUN uv sync --frozen --no-dev \
 && mkdir -p /data/bridge /data/signal-cli \
 && chown -R 1000:1000 /data /app

# uid 1000 in eclipse-temurin:25-jre-noble is the base image's own `ubuntu`
# account — asserted at build time so a base-image change cannot silently
# break the bind-mounted account store (same check deploy/signal-cli runs).
RUN test "$(id -un 1000)" = "ubuntu" || (echo "uid 1000 changed in base image" && false)
USER 1000:1000

ENV XDG_DATA_HOME=/data \
    NORA_BRIDGE_JOURNAL=/data/bridge/journal/signal.jsonl \
    NORA_BRIDGE_ATTACH_DIR=/data/bridge/attachments

EXPOSE 18080 18090

ENTRYPOINT ["/app/.venv/bin/python", "/app/wrapper.py"]
