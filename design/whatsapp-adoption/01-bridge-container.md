---
type: design
title: "WhatsApp Bridge — Container, Pairing, and Operations"
description: "The whatsapp-bridge compose service: build, private-network wiring, session volume, first-run QR pairing, health, and the failure modes that matter."
tags: [nora, whatsapp, baileys, docker, container, pairing]
timestamp: '2026-09-04'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: ../docker-compose.yml, rel: precedent-signal-cli, provenance: asserted}
---

# WhatsApp Bridge — Container, Pairing, and Operations

## Service shape (mirrors signal-cli)

The bridge is a new compose service in the **same project** as Nora, on the
**private `nora` network**, publishing **nothing** — exactly the trust cutover
signal-cli took in 2026-08-07 (Option 2): co-networked containers reach it as
`whatsapp-bridge:PORT`; no host port map, no netns outside the project.

```yaml
services:
  whatsapp-bridge:
    build:
      context: ./deploy/whatsapp-bridge
      network: host          # standing constraint 1: BuildKit bridge can't
                             # resolve deb.debian.org (systemd-resolved stub)
    image: whatsapp-bridge:0.1.0
    container_name: whatsapp-bridge
    restart: unless-stopped
    networks: [nora]
    volumes:
      # Baileys multi-file auth state (encryption keys, device creds).
      # Losing this loses the pairing — not just history. Same wall as
      # ./state/signal-cli: deliberately NOT under ./data (Nora must not
      # read transport credentials).
      - ./state/whatsapp:/app/session
    command: ["node", "bridge.js", "--port", "18081",
              "--session", "/app/session"]
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS -m 5 http://127.0.0.1:18081/health || exit 1"]
      interval: 60s
      timeout: 10s
      start_period: 60s
      retries: 3
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "3"}
```

Facts that justify each decision:

- **Private network + no publish.** signal-cli precedent, documented in the
  compose file's own comments (2026-08-07 cutover). The bridge holds full
  account credentials; the narrower the netns, the smaller the blast radius.
- **`restart: unless-stopped`.** Same as signal-cli.
- **Volume under `./state/`.** Matches `./state/signal-cli`. One place to
  look, back up, restore. `chmod 700` on the directory; never commit.
- **Port 18081.** Adjacent to signal-cli's 18080; convention, not protocol.
  Only reachable on the `nora` net.

## The pairing step (the one thing signal-cli doesn't have)

Baileys links as a WhatsApp-Web companion device. WhatsApp has no headless
registration API, so first run is interactive — one of two clean paths:

1. **Foreground container pairing (chosen).** `docker compose run --rm
   whatsapp-bridge --pair` prints a QR (qrcode-terminal), the operator scans
   it from the bot number's phone app (Settings → Linked Devices → Link a
   Device → scan), the process persists the session to the volume and exits.
   Subsequent normal `docker compose up -d` boots with the saved session.
2. **Host-side pairing, hand off session.** Run `node bridge.js` on the host
   with `--session ~/workspace/nora/state/whatsapp`, scan, stop, then start
   the container against the same volume. Equivalent; heavier.

The QR refreshes every ~20s during pairing. If the operator messes it up,
re-run the same command — no cleanup needed.

## Session persistence & re-pairing

- Session survives restarts; the container does NOT re-scan on boot.
- Session invalidation happens when: the phone is reset, the device is
  manually unlinked, or the WhatsApp Web protocol changes upstream.
- **Phone liveness requirement:** in multi-device mode, companions work while
  the phone is offline, but the primary device must come online roughly every
  14 days or the link drops. Budget this like signal-cli's device
  registration: it is an operational fact, not a bug.
- Re-pair = run the foreground pairing command again. No data loss: the
  session dir is replaceable by design (the audit DB is the record).

## Health

Two layers, exactly the signal-cli lesson (the compose file says it: a
process-level check cannot prove the upstream websocket is alive):

1. **Container healthcheck** → `GET /health` on the bridge HTTP port. Proves
   the process serves. Cannot prove the WhatsApp websocket is connected.
2. **Nora `/health`** → surfaces `whatsapp.last_event_age_seconds` (delta:
   publish an SSE event at least once per N minutes; if the bridge is alive
   but the WhatsApp socket died, this ages). `deploy/watchdog.sh` alerts on
   it. This is the real detector.

## Failure modes to document in the runbook

| Failure | Symptom | Action |
|---|---|---|
| WhatsApp protocol update | bridge reconnect loop / messages stop | bump Baileys pin, rebuild, re-pair if session invalidated |
| Phone offline >14 days | device unlinked | re-pair from phone app + foreground command |
| Session dir lost/corrupt | auth error at boot | re-pair (no data loss; DB is the record) |
| Account restricted (ban) | login attempts throttled/rejected | stop using the number; evaluate cause; see 02 §risk posture |
| Network blip | transient disconnect | bridge reconnects automatically (Baileys reconnect scheduler) |

## Dockerfile sketch

```dockerfile
FROM node:20-slim
WORKDIR /app
COPY bridge.js bridge_helpers.js package.json package-lock.json ./
RUN npm ci --omit=dev
EXPOSE 18081
CMD ["node", "bridge.js", "--port", "18081", "--session", "/app/session"]
```

The image builds from `deploy/whatsapp-bridge/`, mirroring
`deploy/signal-cli/`. The Baileys bridge code is adapted from Hermes'
`scripts/whatsapp-bridge/` (MIT) — same endpoints, Nora-specific tweaks (SSE
push instead of long-poll; no Hermes env coupling; `reply_prefix` handling
moved into Nora's egress layer like Discord).
