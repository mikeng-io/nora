FROM python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
# Stdlib only — the collector's otlp_http exporter sends OTLP/JSON, so this
# image installs nothing and pins nothing but the base (the same verified
# trixie digest every other Python image here uses).
COPY sink.py /srv/sink.py
EXPOSE 4318
CMD ["python", "/srv/sink.py"]
