---
type: design
title: "WhatsApp Identity, Policy, and Risk Posture"
description: "whatsapp: phone id-kind, room admission via policy.toml, and the ban-risk operating rules that make personal-account WhatsApp viable for a community agent."
tags: [nora, whatsapp, identity, policy, ban-risk]
timestamp: '2026-09-04'
edges:
  - {dst: ../10-identity-and-policy-schema.md, rel: extends, provenance: asserted}
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
---

# WhatsApp Identity, Policy, and Risk Posture

## Identity: one more platform vocabulary entry

Nora's identity layer (`nora/domain/identity.py`) fixes a per-platform id-kind
vocabulary; a kind outside a platform's set is a startup error, not a typo.
**The WhatsApp entry already exists on master** (`identity.py:55`):

- `signal`: `{phone, uuid}`; `telegram`: `{user_id}`; `discord`: `{user_id}`
- **`whatsapp`: `{phone, lid}`** — already present, with `lid` anticipated.

So the identity work is **not** "add the vocabulary entry" (done); it is
implementing the adapter, LID handling, and policy wiring against an
already-planned platform.

Consequences, all inherited from the existing design:

- A person's WhatsApp number (or LID) is a matchable id form, joining their
  Signal number/uuid and Discord id under one `person_id`.
- `display_name` remains unmatchable on every platform (§1.3) — a WhatsApp
  display name alone can never resolve a person.
- The same-person-across-platforms resolution (a friend in a Signal group
  also messaging Nora on WhatsApp) works for free once the vocabulary entry
  and the `persons.yaml` account block exist.

Pairing a person's WhatsApp to their existing record is a manual edit in
`persons.yaml` (startup-only, no hot reload) — the same rule as every
platform Miles confirmed: manual, never automatic.

**LID resolution is mandatory, not optional.** Baileys delivers many senders
as LID JIDs (`60000000000001@lid`), especially in groups — the Hermes bridge
precedent builds a LID→phone reverse map from session files
(`scripts/whatsapp-bridge/bridge.js:252`, `:606`). The adapter contract must
include the same resolution step, and identity matching must accept both
`phone` and `lid` forms. Without it, group messages whose sender surfaces as
a LID fail person resolution → R-31-style refusal for people who are actually
known.

## Policy: room admission

WhatsApp chats (1:1 and groups) are admitted through the same policy
mechanism as every Nora room, per `design/10-identity-and-policy-schema.md`:

- New WhatsApp chat → not in policy → **fail closed** (R-31 spirit): Nora
  logs, does not reply, does not feed Honcho.
- Operator adds a room entry (`platform = "whatsapp"`, `conversation_id` =
  the normalized WhatsApp JID — see JID contract below) → **recreate** →
  admitted. Recreate, not restart: the single-file bind-mount inode gotcha
  silently served a stale policy.toml twice on 2026-08-08
  (`nora/obs/health.py:14-21` — "a recreate is needed, not a restart").
- `tools` / `location` stay omitted (deny-by-default). Room labels are unique
  within the unit; several rooms per platform are allowed (room-topology
  migration step 3 supersedes the old one-room-per-platform convention).
  (DEC-105 is the `send_to` relay
  rule — "one room may speak into another, bounded by the unit",
  `design/09-decision-index.md:232` — not a room-count rule; do not cite it
  as one.)

**JID normalization contract (must be pinned before implementation).**
Policy matching is exact-string on `conversation_id`; a wrong or
un-normalized form silently denies every WhatsApp room (fail-closed, but
silently inert). The adapter must define, and contract-test:

- 1:1 room id: `phone@s.whatsapp.net` (normalized from the LID/phone form
  the event carries)
- group room id: `id@g.us`
- which Baileys field feeds `conversation_id`, and how LID/phone forms
  normalize before matching

Verification path: `load_policy` in the repo host venv (never
`docker exec nora python` — container lacks pydantic), then
`docker compose up -d --force-recreate nora`, then confirm `/health`
`config.policy.disk_mtime` matches the edited file's mtime.

## Engagement: what triggers Nora on WhatsApp

Nora's live engagement posture is **voice-note + @mention/reply only** —
the text trigger is disabled (`config.toml:243-249`, operator 2026-09-01:
substring match fired on a plain URL). The WhatsApp adapter must therefore
define, and contract-test, what "mention/reply" means on this platform:

- **Group:** bot number present in the message text? A reply-to-a-Nora-
  message? Both? (WhatsApp has no native @mention syntax the way Discord
  does — the adapter must pick a deterministic signal.)
- **1:1:** any message from an admitted person (DM semantics pending the
  DM-study session — see sequencing in `00-overview.md`).
- **Voice notes:** an engagement signal in Nora's current posture, and
  this is the ACTUAL behavior today: `VoiceNotePolicy` is global
  (`nora/app/composition.py:1124`, operator directive 2026-08-26 總之任何
  Voice Note 都會直入上 Nora 度先囉) and fires on any `voice_note` MediaRef
  even with no transcript. Media custody + transcription (fetch_token) is
  the later phase, but the note engages her now — this is stated, per
  Claude audit H-1, rather than the docs claiming engagement waits.

The old "quoted trigger token" framing is superseded: it describes the
pre-2026-09-01 posture and must not be implemented.

## Ban-risk posture (the core caveat)

Hermes' WhatsApp docs are blunt: using a third-party bridge "carries a small
risk of account restrictions", and WhatsApp periodically updates the Web
protocol, temporarily breaking bridges. The operating rules that keep this
acceptable:

1. **Dedicated number, never a personal one.** A separate WhatsApp
   registration (see below) is the single biggest risk reduction. Miles's
   personal number is never the bot.
2. **Reply-only, conversational.** No bulk sends, no broadcasts, no
   outbound to people who have not messaged Nora first. The ambient design
   already enforces this shape: Nora is silent by default.
   **One bounded exception (DEC-287, 2026-10-05):** a one-time code the person
   asked for, to their own account in `identity.toml`, at most `[otp]
   max_per_hour` an hour and `max_per_day` a day, and 3 requests per portal
   visitor in 15 minutes. Nobody outside `identity.toml` is ever messaged
   (R-101).
3. **No scheduled/bulk campaigns** through the bridge, ever. If a future
   feature wants to push notifications to many people, that is a Business
   API conversation, not this one.
4. **Accept protocol churn.** When WhatsApp updates break the bridge, the
   fix is: bump the Baileys pin, rebuild, re-pair. Nora's own code rarely
   changes (the transport contract is stable). This is a maintenance cost,
   scheduled like any dependency update.

Dedicated-number options (same menu Hermes documents):

| Option | Cost | Notes |
|---|---|---|
| Prepaid SIM | $5–15 one-time | Activate, verify WhatsApp, then the SIM can sit in a drawer. Number must stay active (a call every ~90 days). |
| VoIP service | free–$5/mo | TextNow/TextFree etc. Some VoIP numbers are blocked by WhatsApp — try a few. |
| Google Voice | free | US only; not applicable from HK without a US number setup. |

Recommendation: a prepaid HK SIM (dual-verified against WhatsApp) — cheapest,
most local, no recurring cost, and the keep-alive call every ~90 days is a
calendar note, not infrastructure.

## What this means for Nora's guarantees

- **R-5 / fail-closed:** unchanged — unknown rooms and unknown senders are
  not answered, on WhatsApp as everywhere else.
- **Privacy:** the bridge holds transport credentials only; it never sees
  Nora's DB. The session volume is the only WhatsApp secret, and it stays out
  of `./data` by construction.
- **Auditability:** every WhatsApp message lands in the same SQLite audit
  trail as Signal/Discord, with the same redaction rules
  (`13-trace-content-and-redaction.md`).

## Open decisions for Miles

1. **Dedicated number source — DECIDED: already available** (operator
   2026-09-04). A dedicated number exists; pairing uses it. No procurement
   step in this bundle.
2. **1:1 DMs — DECIDED: in scope** (operator 2026-09-04). 1:1 DM handling
   is being defined in a separate DM-study session; this bundle wires the
   transport and applies general room rules until that session lands.
   Discord v1 deliberately excluded 1:1 DMs (roster semantics absent) — the
   DM-study session owns whether WhatsApp changes that posture.
3. **Unit mapping — DECIDED: reuse `friends`** (operator 2026-09-04).
   WhatsApp chats join the existing `friends` unit — same roster, second
   ingress. WhatsApp becomes the friends room's WhatsApp surface, parallel
   to its Signal group (and existing Discord rooms). (DEC-105 is the
   `send_to` relay rule, not a room-count rule — the unit decision
   stands on the operator's explicit call.)
