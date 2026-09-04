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
Current vocabulary:

- `signal`: `{phone, uuid}`; `discord`: `{id}` (plus metadata keys)
- **New:** `whatsapp`: `{phone}`

Consequences, all inherited from the existing design:

- A person's WhatsApp number is a matchable id form, joining their Signal
  number/uuid and Discord id under one `person_id`.
- `display_name` remains unmatchable on every platform (§1.3) — a WhatsApp
  display name alone can never resolve a person.
- The same-person-across-platforms resolution (a friend in a Signal group
  also messaging Nora on WhatsApp) works for free once the vocabulary entry
  and the `persons.yaml` account block exist.

Pairing a person's WhatsApp to their existing record is a manual edit in
`persons.yaml` (startup-only, no hot reload) — the same rule as every
platform Miles confirmed: manual, never automatic.

## Policy: room admission

WhatsApp chats (1:1 and groups) are admitted through the same policy
mechanism as every Nora room, per `design/10-identity-and-policy-schema.md`:

- New WhatsApp chat → not in policy → **fail closed** (R-31 spirit): Nora
  logs, does not reply, does not feed Honcho.
- Operator adds a room entry (`platform = "whatsapp"`, `conversation_id` =
  the WhatsApp JID) → restart → admitted.
- `tools` / `location` stay omitted (deny-by-default), honoring the
  DEC-105 one-room-per-platform-per-workspace rule where it applies.

Verification path: `load_policy` in the repo host venv (never
`docker exec nora python` — container lacks pydantic), then
`docker compose restart nora`, then confirm `/health`
`config.policy.disk_mtime`.

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

1. **Dedicated number source** — prepaid SIM (recommended) vs VoIP vs
   deferring until a number exists.
2. **1:1 DMs admitted?** Discord v1 deliberately excluded 1:1 DMs (roster
   semantics absent). WhatsApp 1:1 has the same issue; the recommendation is
   to admit only known `persons.yaml` accounts in 1:1, same as every room
   rule, and defer the general "anyone can DM Nora" question.
3. **Workspace mapping** — do WhatsApp chats join the existing `friends`
   workspace (same people, second ingress) or a fresh workspace? DEC-105
   says one room per platform per workspace; a second Signal group already
   needs its own workspace. Recommendation: reuse `friends` — same roster,
   same people.
