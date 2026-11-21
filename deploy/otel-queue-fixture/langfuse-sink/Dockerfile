FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
# Stdlib only — the collector's otlp_http exporter sends OTLP/JSON, so this
# image installs nothing and pins nothing but the base (the same verified
# trixie digest every other Python image here uses).
COPY sink.py /srv/sink.py
EXPOSE 4318
CMD ["python", "/srv/sink.py"]
