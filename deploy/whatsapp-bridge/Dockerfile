FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea
WORKDIR /app
# curl is load-bearing, not decoration: node:24-trixie-slim ships neither curl nor
# wget, and the compose healthcheck curls /health. Without it the container
# boots but reports unhealthy forever (design 01; review B1).
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY bridge.js bridge_helpers.js ./
# Non-root, matching every other container in this compose project (M-1).
USER node
EXPOSE 18081
CMD ["node", "bridge.js", "--port", "18081", "--session", "/app/session"]
