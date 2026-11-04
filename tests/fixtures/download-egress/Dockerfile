FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg iproute2 openssl util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY yt_dlp /opt/yt_dlp
COPY public-download.py /repo/app/scripts/public-download.py
COPY probe_public_download.py /repo/tests/integration/probe_public_download.py
ENV PYTHONPATH=/opt HOME=/tmp
CMD ["sh", "-c", "ip addr add 93.184.216.34/32 dev lo && exec setpriv --reuid 1000 --regid 1000 --clear-groups python3 /repo/tests/integration/probe_public_download.py"]
