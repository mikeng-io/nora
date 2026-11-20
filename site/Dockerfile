# Nora's public site: built with Node, served as static files by Caddy.
# Build from the REPO ROOT so the art in assets/ is in the context:
#   docker build -f site/Dockerfile -t nora-site .
# (site/Dockerfile.dockerignore keeps that context to site/ and assets/.)
FROM node:24-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS build
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

# Hash every inline script the site ships and bake the hashes into the Caddyfile's
# script-src (no 'unsafe-inline'). Fails the build on an inline event handler or
# javascript: URL, which a hash cannot cover.
FROM node:24-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0 AS csp
WORKDIR /csp
COPY site/scripts/csp-hashes.mjs ./
COPY site/Caddyfile ./Caddyfile.in
COPY --from=build /repo/site/dist ./site-dist
RUN node csp-hashes.mjs Caddyfile.in Caddyfile site-dist

FROM caddy:2-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0
COPY --from=csp /csp/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/site/dist /srv
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
