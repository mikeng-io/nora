FROM node:25-trixie-slim@sha256:aabbe39553d15ede8a97cc60c9e1a97034ff772afcf696ea42b94e7f5f2ec71b
WORKDIR /app
COPY bridge.js bridge_helpers.js package.json package-lock.json ./
# curl is load-bearing, not decoration: node:24-trixie-slim ships neither curl nor
# wget, and the compose healthcheck curls /health. Without it the container
# boots but reports unhealthy forever (design 01; review B1).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN npm ci --omit=dev
# Non-root, matching every other container in this compose project (M-1).
USER node
EXPOSE 18081
CMD ["node", "bridge.js", "--port", "18081", "--session", "/app/session"]
