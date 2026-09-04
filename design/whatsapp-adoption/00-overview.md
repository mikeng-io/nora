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
this; `SignalAdapter` and `DiscordAdapter` both exist today
(`nora/platform/registry.py:36-39`). The WhatsApp adapter is the **third**
realization, following the Discord precedent.

## Sequencing: transport first, DM policy second (operator 2026-09-04)

WhatsApp and the DM study are **not serial** — they touch different layers:

- The **transport** (bridge container, pairing, adapter, ambient wiring) is
  identical regardless of what DM semantics conclude; it carries 1:1 and
  group traffic the same way signal-cli does.
- The **DM study** feeds the policy layer: roster, disclosure rules, and the
  fallback for a roster-less conversation. It is a shared-core question that
  exists independent of WhatsApp (Discord v1 already excluded DMs).

Verdict (pinned here so the build order is not re-litigated):

1. **Build the WhatsApp transport first**, admitting **groups** to `friends`.
2. **Let the DM study run in parallel** (its own session).
3. **When the DM study lands**, flip 1:1 admission on with its rules.

One hard rule while waiting: **1:1 stays deny-by-default** (fail-closed,
R-31 spirit) until the DM study concludes. The transport may *carry* 1:1
traffic, but the adapter must not *admit* 1:1 with general-room rules that
the DM design will likely change — the later refit stays bounded to policy
config, never a transport rework.

Blocking the transport on the DM study would leave the bridge — the part with
real external risk (protocol churn, ban posture, number lifecycle) — idle
while a design discussion finishes.

## What v1 covers

- Dedicated WhatsApp number (bot mode) — **not** Miles's personal number.
- Ambient ingest: every inbound WhatsApp message → SQLite audit + Honcho,
  exactly the Signal path.
- Engagement: reply via bridge on the platform's mention/reply signals.
  **The text trigger is DISABLED in live config** (`config.toml:243-249`,
  operator 2026-09-01 — substring match fired on a plain URL). Nora's current
  posture is voice-note + @mention/reply only; the WhatsApp adapter must map
  "mention/reply" onto WhatsApp semantics (bot number present in a group
  message? reply-to-a-Nora-message?) and voice notes need media custody +
  transcription before they can engage. See `02-identity-and-policy.md` §Engagement.
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
- **Inbound media (images/files/voice) is deferred but not silent.** The
  transport must carry media events to the ambient tier (custody + audit),
  but v1 does not define transcription/description of WhatsApp media. Voice
  notes are an engagement signal in Nora's current posture — deferring them
  means voice-note engagement does not work until media ingestion lands
  (see `02-identity-and-policy.md` §Engagement).
- **No 1:1-DM semantics invented here.** 1:1 DMs are in scope (operator
  2026-09-04) but their semantics — roster, disclosure, fallback for a
  roster-less conversation — are the output of the DM-study session. This
  bundle wires the transport and applies the general room rules only.

## Deployment posture

Containerized in the existing compose project — explicitly NOT a host-side
daemon. Details in `01-bridge-container.md`.
