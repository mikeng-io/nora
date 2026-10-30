# Ingester image with journalctl for the journald receiver (issue #440).
# The contrib journald receiver shells out to `journalctl`, which the stock
# signoz-otel-collector image does not carry. On the Linux deploy host the
# container mounts /var/log/journal + /run/log/journal and /etc/machine-id;
# the cursor survives restart via the file_storage extension.
# Local Docker Desktop has no journald — the filelog receiver path in
# ingester.yaml is the local proof; this image is the Linux-host input.
FROM signoz/signoz-otel-collector:v0.144.15
USER root
RUN if command -v microdnf >/dev/null 2>&1; then microdnf install -y systemd-libs systemd-udev && microdnf clean all; \
    elif command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y --no-install-recommends systemd && rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then apk add --no-cache systemd-libs || apk add --no-cache eudev-libs libcap; fi
RUN command -v journalctl || (echo "journalctl missing; image incompatible" && exit 1)
