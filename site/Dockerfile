# Nora's public site: built with Node, served as static files by Caddy.
# Build from the REPO ROOT so the art in assets/ is in the context:
#   docker build -f site/Dockerfile -t nora-site .
# (site/Dockerfile.dockerignore keeps that context to site/ and assets/.)
FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS build
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
FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS wiki
# Native wiki regressions create Git fixtures and independently verify the patch.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /repo/docs-site
COPY docs-site/package.json docs-site/package-lock.json ./
RUN npm ci --ignore-scripts
COPY docs-site/ ./
RUN npm run postinstall
COPY openwiki/ /repo/openwiki/
COPY assets/nora-canonical-self.png assets/nora-mini-icon.png /repo/assets/
ARG SITE_ORIGIN
RUN npm run build

FROM caddy:2-alpine@sha256:24ec7b05bc532c7bb57f727030c5056e257ff7138b4b50460306819ff79362bd
COPY site/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/site/dist /srv
COPY --from=wiki /repo/docs-site/dist /srv/wiki
# The site listens on 8080 and has admin/persist_config off, so it never needs
# root. The official caddy image has no unprivileged account (it runs as root),
# so create one, give it the state dirs it would otherwise have to create as
# root, then drop privileges.
RUN addgroup -S caddy \
    && adduser -S -D -H -G caddy caddy \
    && mkdir -p /data/caddy /config/caddy \
    && chown -R caddy:caddy /data /config
USER caddy
EXPOSE 8080
