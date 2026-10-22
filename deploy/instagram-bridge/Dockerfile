# instagram-bridge — build and runtime.
#
# Two stages: the Go toolchain never ships to production, and the runtime
# carries a single static binary plus the two things it genuinely needs
# (CA certificates to speak TLS to instagram.com, and curl for the compose
# healthcheck).

FROM golang:1.27.3-bookworm@sha256:972b4110ba0f47539a975c31e52c64765e1ff19f2d74469ae4a595c6676e80a5 AS build
WORKDIR /src

# go.mod and go.sum first so the module download layer survives a code edit.
#
# go.mod carries a `replace` for github.com/imroc/req/v3 that is NOT optional:
# mautrix-meta declares the same replace in ITS go.mod and Go honours replace
# directives only from the MAIN module, so without it the build fails on
# quic-go with errors that name neither Instagram nor req. The comment on the
# directive itself has the detail.
COPY go.mod go.sum ./
RUN go mod download

# The source is a TREE (design/instagram-adoption/16 §Layers): the composition
# root in cmd/ and the layers in internal/. Copied as directories, because a
# `*.go` glob at the root would copy nothing and the build would fail on an
# import it cannot resolve.
COPY cmd ./cmd
COPY internal ./internal

# CGO off, so the result is a static binary that runs on a slim runtime with no
# libc version to match. -trimpath keeps build paths out of the binary.
#
# `go vet` and the tests run HERE, in the build, rather than only in someone's
# shell. Nothing in this bridge can be verified against the live account — it
# is a real account in daily use — so the unit suite is the only gate there is,
# and a gate that has to be remembered is not a gate. That includes the layer
# rule: cmd/instagram-bridge/architecture_test.go runs `go list -deps` here.
#
# IG_BRIDGE_IMAGE_BUILD=1 tells ONE test where it is: the shared contract
# fixtures (tests/fixtures/instagram_bridge) are outside this build context,
# so contract_fixtures_test.go skips here and runs wherever the repo is.
RUN go vet ./... && IG_BRIDGE_IMAGE_BUILD=1 go test ./... && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/instagram-bridge ./cmd/instagram-bridge

FROM debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8

# ca-certificates is not optional: every request to instagram.com is TLS and a
# slim image ships no trust store, so without it the bridge fails at LoadIndex
# with an x509 error that reads like a rejected session.
#
# curl is load-bearing for the same reason it is in whatsapp-bridge and
# signal-cli: the compose healthcheck curls /health, and debian:bookworm-slim
# ships neither curl nor wget. Without it the container boots, the healthcheck
# fails forever, and `unhealthy` reads as a dead bridge — an ERROR presenting
# as a PASS's opposite, on a component whose whole point is to distinguish
# "checkpointed" from "down".
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /out/instagram-bridge /usr/local/bin/instagram-bridge

# uid/gid 1000, matching signal-cli's container and every other service in this
# compose project, so the bind-mounted session directory needs no chmod dance
# or runtime `user:` override. The account is CREATED here rather than assumed:
# debian:bookworm-slim ships no uid 1000 at all, so the `id -un 1000` guard
# this file used to carry — copied from deploy/signal-cli, whose base is Ubuntu
# noble and does ship `ubuntu` at 1000 — failed the build outright. Same shape
# as deploy/stt, deploy/drop and deploy/voice-wake.
RUN groupadd --gid 1000 bridge \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/bridge \
       --shell /usr/sbin/nologin bridge
USER 1000:1000

# No VOLUME declaration: compose bind-mounts the real session store at
# /session. Declaring one makes Docker create a throwaway anonymous volume on
# every recreate — the same trap signal-cli's Dockerfile records.
EXPOSE 18082

ENTRYPOINT ["instagram-bridge"]
CMD ["--port", "18082", "--session", "/session"]
