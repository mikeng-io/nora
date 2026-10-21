# Stdlib only — see bridge.py's docstring. No pip install, nothing to pin, and
# nothing that can fail to resolve during an incident.
FROM python:3.12-slim-trixie
WORKDIR /app
COPY bridge.py /app/bridge.py
RUN useradd -r -u 10001 bridge && chown -R bridge /app
USER bridge
EXPOSE 9110
CMD ["python", "-u", "/app/bridge.py"]
