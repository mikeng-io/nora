---
type: design
title: "Instagram Adoption — Overview, Verdict & Phases"
description: "Measured feasibility for Instagram group DMs as Nora's fourth ingress: a Go bridge daemon wrapping mautrix's instameow, mirroring how whatsapp-bridge joined. Includes the three id spaces that will bite, and the three phases the operator scoped."
tags: [nora, instagram, instameow, mautrix, container, platform-adapter]
timestamp: '2026-09-21'
edges:
  - {dst: ../whatsapp-adoption/00-overview.md, rel: precedent, provenance: asserted}
  - {dst: ../discord-integration/00-overview.md, rel: precedent, provenance: asserted}
  - {dst: ../01-ambient-ingest.md, rel: builds-on, provenance: asserted}
  - {dst: 01-bridge-container.md, rel: details, provenance: asserted}
  - {dst: 02-identity-and-policy.md, rel: details, provenance: asserted}
  - {dst: 03-transport-contract.md, rel: details, provenance: asserted}
  - {dst: 04-capability-evidence.md, rel: evidence, provenance: measured}
  - {dst: 05-phase2-social-and-opencli.md, rel: details, provenance: asserted}
  - {dst: 06-retention-and-custody.md, rel: details, provenance: asserted}
  - {dst: 07-social-boundary-and-scope.md, rel: details, provenance: asserted}
  - {dst: 08-social-adapter-architecture.md, rel: details, provenance: asserted}
  - {dst: 09-tool-surface-and-naming.md, rel: details, provenance: asserted}
  - {dst: 10-discovery-retrieval-and-paging.md, rel: details, provenance: asserted}
  - {dst: 11-sequencing-and-flows.md, rel: details, provenance: asserted}
---

# Instagram Adoption — Overview, Verdict & Phases

## Why Instagram, why now

Instagram is the fourth ingress after Signal, Discord and WhatsApp, and it is
the first one where the **group** is not the only thing worth reaching. The
other three are messengers; Instagram is a messenger attached to a social
graph. That is why the operator scoped this in three phases rather than one,
and why this bundle deliberately separates "reach the group" from "read the
graph around the group".

This bundle covers the Instagram ingress and the social reads it makes
possible. Everything else in Nora — music, recall, dreamer, standing — is
platform-agnostic and needs no Instagram-specific design.

## Feasibility verdict (measured 2026-09-21)

**Feasible for group DMs, but only off the official road.** Meta's own
documentation is unambiguous:

> "Group messaging is not supported. An Instagram professional account can
> only converse with one customer per conversation."
> — [Instagram Platform, Send Messages](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/)

The sanctioned API gives 1:1 threads, user-initiated, inside a 24-hour window.
That is not a room, so it cannot carry an ambient agent under DEC-105. Every
path to a group thread emulates a real Instagram client.

**What was measured, not assumed.** A standalone Go probe
(`igprobe`/`igcap`/`igmedia`, scratchpad, not in this repo) was built against
`go.mau.fi/mautrix-meta/pkg/instameow` and run against a live account:

| Check | Result |
|---|---|
| Links and runs with no Matrix homeserver, appservice or bridgev2 runtime | PASS |
| Authenticates from browser cookies | PASS — `@not_____nora` |
| Enumerates group threads | PASS — 1 group visible |
| Receives a live group message over the realtime socket | PASS |
| Inbound `@`-mention arrives as **structure** (`mentions[]`), not plaintext | PASS |
| Sends text / mention / reply / reaction / edit / typing / unsend | PASS |
| Uploads image bytes and sends them to the group | PASS |
| Reads another member's profile and **stories** | PASS — `FetchReel` status=ok |
| Rejects text over the real ceiling | 20,000 accepted; 50,000 refused |

Full evidence, including the exact wire payloads, is in
`04-capability-evidence.md`.

## Library landscape

| Option | Language | Group DMs | Realtime | Standalone | License |
|---|---|---|---|---|---|
| **`instameow`** (in `mautrix/meta`) | Go | yes | MQTT/thrift | yes (measured) | AGPL-3.0 |
| `instagrapi` / `aiograpi` | Python | yes | polling | yes | MIT |
| Unipile (hosted) | HTTP | yes | webhooks | n/a | commercial |
| Instagram Messaging API (official) | HTTP | **no** | webhooks | n/a | — |

**Chosen: `instameow`.** Reasons:

1. **It is the only maintained realtime client for the CURRENT protocol.**
   Meta disabled the old Instagram DM API around August 2026; the old
   `mautrix-meta` Instagram mode stopped working entirely and was replaced by
   a separate `mautrix-instagram` binary against a new protocol. `instagrapi`
   tracks the Android client and is HTTP-polling shaped — workable for reads,
   wrong for ambient presence.
2. **Same author, same shape as whatsmeow**, which is the substrate the
   WhatsApp ecosystem already trusts. `pkg/instameow` is that pattern applied
   to Instagram: MQTT socket, thrift codecs, `DumpState`/`LoadState` session
   resume, an event handler, and a complete send surface.
3. **It is genuinely separable.** `instameow` does import
   `maunium.net/go/mautrix/bridgev2`, but every one of those references is
   confined to `login_*.go` and is a plain descriptive struct
   (`bridgev2.LoginStep`, `LoginInputDataField`) meaning "which form field to
   show next". There are **zero** bridgev2 references in `client.go`,
   `socket.go`, `route.go` or `streamcontroller.go`. The wire is clean.

### The dependency trap

`mautrix-meta`'s `go.mod` carries

```
replace github.com/imroc/req/v3 => github.com/beeper/req/v3 v3.0.0-20260808092153-100cef0a2fbd
```

Go **ignores `replace` directives from dependencies**. A standalone consumer
resolves the real `imroc/req`, which does not compile against
`quic-go v0.61.0` — the first build died on eleven `undefined:
quic.ConnectionTracingID` errors. The bridge daemon's own `go.mod` must copy
that line verbatim. This is not optional and there is no warning.

## Architecture fit

A **transport twin of `whatsapp-bridge`**, not a new Nora component:

```
Instagram (Meta servers, no E2EE since 2026-05-08)
        │  cookie session + MQTT realtime
        ▼
instagram-bridge (Go, instameow)  ── compose service, private `nora` net
        │  SSE push events + HTTP send
        ▼
Nora ambient parser (existing)    ── zero-delta: same event contract
        │
        ├─ SQLite audit append
        └─ Honcho feed
              │
              ▼
tag detection → Strands agent → send_reply → bridge HTTP send
```

`PlatformAdapter` and `AdapterRegistry` already carry Signal, Discord and
WhatsApp (`nora/platform/registry.py`). Instagram is the **fourth**
realization and needs no change to the port.

## Phases (operator, 2026-09-21)

**Phase 1 — parity.** Everything Discord and Signal already do: send text,
send images, see images, see video, mentions, replies, emoji reactions.
Measured as reachable; see `04-capability-evidence.md` for what is proven and
what is still open.

> **Instagram is the first social NETWORK Nora adopts, not a fourth
> messenger.** The messaging and social axes are separate ports joined only by
> the person index, and reach is bounded by a four-tier ladder. Read
> `07-social-boundary-and-scope.md` before designing anything on the social
> side; it supersedes the framing in `05`.

**Phase 2 — social fetching.** Read the graph around the group: the stories,
posts and profiles of the people in it, where the account follows them.
`instameow` already exposes `GetProfile`, `FetchReel` (stories), `FetchMedia`
(posts) and `SearchUsers` **on the same session** — no second credential, no
second library. Stories are the part nothing in Nora can reach today. This
phase is mostly transport-complete and mostly **policy**-incomplete: see
`05-phase2-social-and-opencli.md`.

**Phase 3 — the wider feed.** Reach posts beyond the group's members,
reusing the existing OpenCLI runtime rather than widening this bridge's
surface. Phase 2 and 3 are **not greenfield**: DEC-151's `social_fetch`
already covers Instagram profile/posts/search through OpenCLI, behind a
host-side allowlist that is the security boundary. The recommendation is to
keep that tool and add instameow as a *backend* behind it, closing two of its
recorded limitations and adding stories. See
`05-phase2-social-and-opencli.md`.

## Known restrictions (parked)

Recorded so they are decisions rather than surprises. None blocks Phase 1.

| # | Restriction | Status |
|---|---|---|
| R1 | **View-once and allow-replay media cannot be persisted.** `view_mode` 0 and 1 serve no bytes at all to a client that has not opened them — `content.attachment` is `null` in the live delta and in history alike. Only "keep in chat" (`view_mode` 2) carries retrievable media. | **PARKED.** Persisting them would require *opening* them, a deliberate state change. v1 records that one-time media was sent, by whom and when, with no bytes — what a person in the room who did not tap it also has. |
| R2 | **History pagination is broken.** `PaginateMailbox` and `PaginateMessages` both fail with `Query Error`; `GetThread` returns recent history fine. | Open. Makes the message-requests folder unreadable, and makes session resume load-bearing rather than optional. |
| R3 | **Text with media is two messages.** `SendMediaRequest` has no text field. | Settled; inverts the WhatsApp caption pattern. |
| R4 | **One reaction per message.** | Settled; narrower than Signal and Discord. |

**What is NOT restricted, and is the thing that matters most:** the bridge
*listens*. `Connect` opens Instagram's MQTT stream and the server pushes —
text, mentions, replies, images, video, stickers, one-time announcements,
typing, and member events all arrived live and unprompted during measurement.
Live ingest is the best-established capability in this bundle; history is the
weak one, which is the reverse of the usual failure.

## What this costs

Three risks, in the order they will actually bite:

1. **Nora will refuse to start.** `AdapterRegistry.from_config` is fail-fast
   by design: any enabled platform that cannot resolve its own identity raises
   `AdapterRegistryError` and becomes a `StartupError`. Instagram checkpoints
   are routine, not exceptional. Unless enabled-but-unauthenticated is made a
   survivable state, one captcha takes Signal and Discord down with it. This
   is the change to make **before** any Go is written — see
   `01-bridge-container.md` §Degradation.
2. **Account loss is unappealable.** Detection is a moving target and a client
   that works today can trigger a cohort ban tomorrow. Losing the account
   means every group re-adds Nora by hand.
3. **AGPL-3.0.** `instameow` is AGPL. Process separation — the bridge as its
   own container, talking HTTP — keeps Nora itself out of the derivative-work
   question. Linking it in would not. `THIRD_PARTY_NOTICES.md` needs the entry
   either way.

And one that is not technical: **Instagram has no bot concept.** To everyone
else in a group thread Nora is a person, indistinguishable from a human
account, while the room allowlist records every sender into Dreamer notes. On
Discord a bot is visibly a bot. Here it is not, unless the operator says so.

## Sequencing

1. Make Instagram's absence survivable in `AdapterRegistry` (no new deps).
2. Measure session longevity — **in parallel, gating nothing**. The soak on
   norty (`~/workspace/ig-soak/`, `status.sh`) tunes re-auth cadence, which is
   a runbook number rather than an architectural one. An earlier revision said
   no bridge should be written until it produced a number; that was
   over-cautious and is withdrawn (`11-sequencing-and-flows.md`).
3. Phase 1 bridge + adapter, group-only, deny-by-default on DMs, mirroring
   WhatsApp v1.
4. Phase 2 social reads, behind their own policy flag.
5. Phase 2 needs **one** operator answer: whether story reads are permitted
   at all, since a story read is visible to its author and no existing Nora
   read is. The account question is settled — `opencli instagram whoami`
   confirms the social browser profile holds the same `@not_____nora`
   account, so adopting instameow removes a session rather than adding one.
   See `05-phase2-social-and-opencli.md`.
6. Phase 3 stays on OpenCLI. `instameow` should not grow into a general
   scraper; scoping it to the group and its members is what keeps the DM
   session's risk profile defensible.
