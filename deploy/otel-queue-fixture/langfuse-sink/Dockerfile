FROM python:3.15-slim-trixie@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0
# Stdlib only — the collector's otlp_http exporter sends OTLP/JSON, so this
# image installs nothing and pins nothing but the base (the same verified
# trixie digest every other Python image here uses).
COPY sink.py /srv/sink.py
EXPOSE 4318
CMD ["python", "/srv/sink.py"]
