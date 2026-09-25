# Nora's public site: built with Node, served as static files by Caddy.
# Build from the REPO ROOT so the art in assets/ is in the context:
#   docker build -f site/Dockerfile -t nora-site .
# (site/Dockerfile.dockerignore keeps that context to site/ and assets/.)
FROM node:22-slim AS build
WORKDIR /repo/site
COPY site/package.json site/package-lock.json ./
RUN npm ci
COPY assets/ /repo/assets/
COPY site/ ./
ARG SITE_ORIGIN
ARG PUBLIC_PORTAL_ORIGIN
RUN npm run build

FROM caddy:2-alpine
COPY site/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/site/dist /srv
EXPOSE 8080
