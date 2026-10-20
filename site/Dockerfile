# Nora's public site: built with Node, served as static files by Caddy.
# Build from the REPO ROOT so the art in assets/ is in the context:
#   docker build -f site/Dockerfile -t nora-site .
# (site/Dockerfile.dockerignore keeps that context to site/ and assets/.)
FROM node:22-slim AS build
WORKDIR /repo/site
COPY site/package.json site/package-lock.json ./
# --ignore-scripts: esbuild's install script only re-validates the binary its
# platform package already ships, and on norty it races into ETXTBSY; sharp
# and esbuild both load their prebuilt platform packages without it.
RUN npm ci --ignore-scripts
COPY assets/ /repo/assets/
COPY site/ ./
ARG SITE_ORIGIN
ARG PUBLIC_PORTAL_ORIGIN
RUN npm run build

# The wiki is generated/reviewed in Git; image builds make no model calls.
FROM node:22-slim AS wiki
WORKDIR /repo/docs-site
COPY docs-site/package.json docs-site/package-lock.json ./
RUN npm ci --ignore-scripts
COPY docs-site/ ./
COPY openwiki/ /repo/openwiki/
ARG SITE_ORIGIN
RUN npm run build

FROM caddy:2-alpine
COPY site/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/site/dist /srv
COPY --from=wiki /repo/docs-site/dist /srv/wiki
EXPOSE 8080
