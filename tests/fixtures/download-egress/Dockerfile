FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg iproute2 openssl util-linux \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir /control && chmod 0777 /control
COPY yt_dlp /usr/local/lib/python3.12/site-packages/yt_dlp
COPY public-download.py /repo/app/scripts/public-download.py
COPY probe_public_download.py /repo/tests/integration/probe_public_download.py
ENV HOME=/tmp
CMD ["sh", "-c", "exec setpriv --reuid 1000 --regid 1000 --clear-groups python3 /repo/tests/integration/probe_public_download.py"]
