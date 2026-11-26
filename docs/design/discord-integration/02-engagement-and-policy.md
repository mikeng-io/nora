---
type: design
title: "Discord Engagement Model & Channel Policy"
description: "Ambient+tag parity with Signal, scoped to explicitly allowlisted channels; policy.toml schema extension for platform-qualified rooms; server-install vs. channel-level enablement."
tags: [nora, discord, policy, engagement, r-5]
timestamp: '2026-08-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-listener-and-resilience, rel: depends_on, provenance: asserted}
---

# Discord Engagement Model & Channel Policy

## Server install ≠ channel enablement

Inviting the bot to a Discord server (OAuth2 bot-invite) grants it technical
visibility into whatever channels its role can see — that is Discord's
permission model, not Nora's. It must not become Nora's behavioral default.
Decided during brainstorming (2026-08-14): Nora should not be ambient in
every channel of every server she's invited to.

## policy.toml stays deny-by-default (R-5)

Nora is ambient in a Discord channel **only** if that channel is explicitly
allowlisted — the same posture `policy.toml` already enforces for Signal
groups, where an empty or missing allowlist means Nora refuses to start.
Discord doesn't get a looser default just because the bot's technical reach
is broader.

**Schema implication:** `policy.toml`'s room entries currently identify a
bare Signal group id. Supporting a second platform means room entries need
to be platform-qualified (platform + server/guild id + channel id), so the
same file can allowlist a Signal group and a Discord channel side by side.
The exact TOML shape is a build-time decision — pinning it here would drift
out of sync with whatever the implementation actually lands on; the
constraint that must hold is deny-by-default across both platforms.

## Inside an allowlisted channel: Signal parity

Decided (2026-08-14): ambient behavior matches Signal exactly, not a
reduced/tag-only mode. In an allowlisted channel:

- Every message is silently read and stored (Postgres audit + Honcho feed),
  same as ambient ingest today.
- Nora replies only when tagged.
- The same guardrail stack applies unchanged — R-27's three-state tool
  result, R-18/R-28's no-identifier/coordinate-egress rules, R-31's
  disclosure fail-closed-on-any-unknown.
- Heartbeat/fire-path eligibility (if enabled) is not platform-specific;
  Discord conversations are eligible on the same terms Signal ones are.

## No 1:1, but the model must not preclude it later

Per `00-overview.md`, Discord DMs are out of scope for v1. The
conversation/room identifier introduced for Discord channels must be shaped
so a DM-style conversation (no roster) can be represented as a future
variant, not bolted on by redesigning the engagement path. This is a
constraint on the identifier scheme now, not an implementation of DMs.
