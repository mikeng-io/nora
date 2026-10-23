# signal-cli, built from the upstream release tarball.
#
# Upstream publishes NO official Docker image — only tarballs — so building from
# the signed release is the trustworthy path rather than an unofficial image.
# The signature is verified HERE, in the build, not only by fetch.sh: verifying
# at fetch time and trusting the file at build time is a TOCTOU gap, and a stale
# tarball left by an interrupted fetch would be silently trusted.
#
# JVM build, not GraalVM native: native's advantage is startup latency, which is
# irrelevant for a daemon that runs for months, while the JVM path is upstream's
# best-tested and yields real stack traces. libsignal is JNI, so this needs
# glibc — Debian/Ubuntu base, never Alpine/musl.
#
# Java 25 is REQUIRED: signal-cli 0.14.7 ships class file version 69. Temurin 21
# fails at class load with UnsupportedClassVersionError.
FROM eclipse-temurin:25-jre-noble@sha256:3f2223fe04b750aa5a8179e29b5c9a1ddf7665fc9bb08bfe11ad01d143bc13a5

ARG SIGNAL_CLI_VERSION=0.14.7
ARG SIGNAL_CLI_KEY=FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg \
 && rm -rf /var/lib/apt/lists/*

COPY signal-cli-${SIGNAL_CLI_VERSION}.tar.gz     /tmp/
COPY signal-cli-${SIGNAL_CLI_VERSION}.tar.gz.asc /tmp/
RUN gpg --batch --keyserver hkps://keys.openpgp.org --recv-keys "${SIGNAL_CLI_KEY}" \
 && gpg --batch --verify /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz.asc \
                         /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz \
 && tar xzf /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz -C /opt \
 && ln -s /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli /usr/local/bin/signal-cli \
 && rm /tmp/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz*

# uid 1000 in eclipse-temurin:25-jre-noble is the base image's own `ubuntu`
# account — we do NOT create a user here, and an earlier version of this file
# claimed to. It works because that uid coincides with the reference host operator (1000), so
# the bind-mounted account store stays writable. If the base image ever changes
# its default uid this breaks silently, so it is asserted at build time below.
RUN test "$(id -un 1000)" = "ubuntu" || (echo "uid 1000 changed in base image" && false)
USER 1000:1000

# No VOLUME declaration: compose bind-mounts the real store at /data/signal-cli,
# and declaring VOLUME ["/data"] made Docker create a throwaway anonymous volume
# on every recreate.
ENV XDG_DATA_HOME=/data

EXPOSE 18080

# Default to LOOPBACK. Binding 0.0.0.0 makes signal-cli DISABLE its Host-header
# check (it says so at startup) on an interface that has no authentication at
# all — so `docker run` for a quick debug must not expose it broadly either.
#
# --receive-mode=on-start is load-bearing. Without it the HTTP server comes up
# and every RPC answers correctly while signal-cli NEVER opens its websocket to
# Signal, so not one message is delivered. The host daemon ran that way for 61
# days with /health green and nora_messages_total at 0.0.
ENTRYPOINT ["signal-cli"]
CMD ["--account", "+85290000100", "daemon", "--http", "127.0.0.1:18080", "--receive-mode", "on-start"]
