FROM node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446
WORKDIR /app
ARG NORA_VERSION=""
ENV OTEL_SERVICE_VERSION=${NORA_VERSION}
# curl is load-bearing, not decoration: node:24-trixie-slim ships neither curl nor
# wget, and the compose healthcheck curls /health. Without it the container
# boots but reports unhealthy forever (design 01; review B1).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY *.js ./
# Non-root, matching every other container in this compose project (M-1).
USER node
EXPOSE 18081
CMD ["node", "bridge.js", "--port", "18081", "--session", "/app/session"]
