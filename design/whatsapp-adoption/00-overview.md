---
type: design
title: "WhatsApp Adoption — Overview & Scope"
description: "Feasibility verdict, architecture fit, and what v1 covers — a Baileys bridge daemon in the Nora compose project feeding the existing ambient tier, mirroring how signal-cli joined."
tags: [nora, whatsapp, baileys, container, platform-adapter]
timestamp: '2026-09-04'
edges:
  - {dst: ../01-ambient-ingest.md, rel: builds-on, provenance: asserted}
  - {dst: ../03-strands-reuse.md, rel: builds-on, provenance: asserted}
  - {dst: ../discord-integration/00-overview.md, rel: precedent, provenance: asserted}
  - {dst: 01-bridge-container.md, rel: details, provenance: asserted}
  - {dst: 02-identity-and-policy.md, rel: details, provenance: asserted}
---

# WhatsApp Adoption — Overview & Scope

## Why WhatsApp, why now

Nora's platform layer already anticipates more than one adapter, and has
already absorbed a second platform once: Discord joined as the community
surface after Signal launched the ambient design (see
`discord-integration/00-overview.md`). WhatsApp is the third ingress, and it
is the one Miles's friend circle most plausibly wants to reach Nora from
without adding an app — WhatsApp is the default messenger in Hong Kong.

This bundle covers **only** the WhatsApp ingress: container, pairing,
identity mapping, and room admission. Everything else (music, media, recall,
dreamer) is platform-agnostic in Nora and needs no WhatsApp-specific design.

## Feasibility verdict (verified 2026-09-04)

**Feasible, with a ban-risk caveat.** There is no official personal-account
API; every path is WhatsApp-Web emulation. The library landscape is healthy:

| Library | Language | Stars | License | Last push |
|---|---|---|---|---|
| Baileys (`WhiskeySockets/Baileys`) | Node/TS | ~10.9k | MIT | 2026-09-03 |
| whatsmeow (`tulir/whatsmeow`) | Go | ~7.2k | MPL-2.0 | 2026-09-03 |
| whatsapp-web.js (`wwebjs/...`) | Node | ~22.5k | Apache-2.0 | 2026-09-03 |

All three were pushed within 24h of this writing and none is archived.

**Chosen: Baileys.** Reasons:

1. **Same stack Hermes already trusts in production.** Hermes' built-in
   WhatsApp bridge (`~/.hermes/hermes-agent/scripts/whatsapp-bridge/`) is a
   standalone Baileys process with a clean HTTP contract (long-poll
   `/messages`, `/send`, `/edit`, `/send-media`, `/health`), pinned to
   `@whiskeysockets/baileys` 7.0.0-rc13 locally. We copy the proven shape, not
   invent a new one.
2. **Node process fits the compose project** the way signal-cli's JVM does.
3. **No browser dependency.** Baileys is socket-based; no Chromium/Puppeteer
   stack (whatsapp-web.js's main operational liability).

## Architecture fit

The daemon is a **transport twin of signal-cli**, not a new Nora component:

```
phone WhatsApp app (primary device)
        │  multi-device pairing (QR)
        ▼
whatsapp-bridge (Baileys, Node)   ── compose service, private `nora` net
        │  SSE push events + HTTP send
        ▼
Nora ambient parser (existing)    ── zero-delta shape: same event contract
        │
        ├─ SQLite audit append
        └─ Honcho feed
              │
              ▼
tag detection → Strands agent → send_reply → bridge HTTP send
```

Nora's `PlatformAdapter` Protocol and `AdapterRegistry` were designed for
this; only `SignalAdapter` exists today. The WhatsApp adapter is the second
realization, matching the Discord precedent.

## What v1 covers

- Dedicated WhatsApp number (bot mode) — **not** Miles's personal number.
- Ambient ingest: every inbound WhatsApp message → SQLite audit + Honcho,
  exactly the Signal path.
- Tag-to-reply: quoted trigger token → Strands agent → reply via bridge.
- Room admission: WhatsApp chats admitted via the same policy mechanism as
  Signal groups / Discord channels.
- Person identity: `whatsapp: phone` id-kind added to the identity
  vocabulary; same-person-across-platforms resolution (a friend on Signal
  and WhatsApp resolves to one `person_id`).
- **1:1 DMs: in scope** (operator 2026-09-04). The transport must carry
  1:1 and group traffic alike; the *semantics* of DM interaction follow the
  separate DM-study session — this bundle does not define them, it only
  wires the ingress.

## What's explicit out of scope for v1

- **No bulk/outbound automation.** Ban-risk posture forbids messaging anyone
  who did not message first (see `02-identity-and-policy.md`).
- **No WhatsApp Business API.** That is a separate official path with its own
  docs (Hermes supports it as `whatsapp-cloud`); not this design.
- **No voice calls.** Only messages.
- **No 1:1-DM semantics invented here.** 1:1 DMs are in scope (operator
  2026-09-04) but their semantics — roster, disclosure, fallback for a
  roster-less conversation — are the output of the DM-study session. This
  bundle wires the transport and applies the general room rules only.

## Deployment posture

Containerized in the existing compose project — explicitly NOT a host-side
daemon. Details in `01-bridge-container.md`.
