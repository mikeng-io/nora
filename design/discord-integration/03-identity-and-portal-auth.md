---
type: design
title: "Discord OAuth SSO & Portal Auth Service"
description: "Discord login for the future web portal, gated to known identity.toml people; a separate lightweight aiohttp auth service sharing Postgres with Nora."
tags: [nora, discord, oauth, portal, identity, r-5]
timestamp: '2026-08-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Discord OAuth SSO & Portal Auth Service

## Login flow

Standard Discord OAuth2 authorization-code flow. On a successful callback,
the portal auth service resolves the returned Discord user id against
`identity.toml`'s per-platform bindings — the same mechanism that already
binds one `person_id` to a Signal account, extended with a
`[[persons.<id>.discord]]` block per account.

## Known-people-only (R-5 parity)

Decided during brainstorming (2026-08-14): a Discord account with no
existing `identity.toml` binding is **refused** at login, not
auto-provisioned. Adding a new binding is a manual `identity.toml` edit,
mirroring how Signal accounts are onboarded today. This is deliberate, not a
missing feature — self-service linking would introduce a new kind of
identity-write path that doesn't exist anywhere in Nora, and isn't justified
at friend-group scale. It also keeps the portal's admission model
consistent with `policy.toml`'s deny-by-default posture rather than
introducing a second, looser gate.

## One person, multiple platforms

This is the first real exercise of the workspace concept described in
`design/proposed/24-honcho-integration-v2.md`: a person's Discord and Signal
accounts both resolve to the same `person_id` / Honcho peer, so recall and
disclosure guardrails (R-18/R-28/R-31) apply uniformly regardless of which
platform they're currently on.

## Deployment: a separate service

The portal auth backend is a new, separate aiohttp service (its own
docker-compose entry, e.g. `nora-portal`) — **not** folded into Nora's main
process.

Rationale: browser-facing HTTP (TLS termination, CORS, session cookies) is a
different traffic and risk shape than Nora's SSE/Gateway ambient pipeline.
Coupling a public-facing web server's uptime and attack surface to Nora's
core reliability isn't justified by the size of what's being built. The
portal service shares Postgres with Nora (a session table alongside the
existing audit schema) and reads `identity.toml` for the known-people gate,
so identity stays single-sourced rather than duplicated into a second store.

The exact session token mechanism (JWT vs. server-side cookie session) is a
build-time decision, left open here.

## Out of scope here

This node designs the auth gate only. The portal's actual conversational
chat UI — what a successful login grants access *to* — is sub-project 2,
not designed in this bundle.
