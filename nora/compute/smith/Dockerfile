FROM python:3.12-slim

# Same reason as nora's/signal-cli's own Dockerfiles (CLAUDE.md operational
# constraint 1): must be built with --network=host on this host, or
# BuildKit's default bridge can't resolve deb.debian.org.
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml .
RUN uv pip install --system --no-cache .

COPY agent.py doctrine.md ./

WORKDIR /workspace
ENTRYPOINT ["python3", "/app/agent.py"]
