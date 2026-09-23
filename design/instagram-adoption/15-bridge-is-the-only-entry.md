---
type: design
title: "The Bridge Is the Only Way In — every Instagram read and write goes through instagram-bridge's HTTP API"
description: "Nora's Python side talks to Instagram through exactly one thing: instagram-bridge's HTTP API. OpenCLI leaves the Instagram rows entirely. How the bridge obtains a fact is the bridge's business; that the bridge is the only door is the rule."
tags: [nora, instagram, bridge, boundary, opencli, decision]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 07-social-boundary-and-scope.md, rel: supersedes, provenance: asserted}
  - {dst: 08-social-adapter-architecture.md, rel: supersedes, provenance: asserted}
  - {dst: 16-bridge-architecture-and-observability.md, rel: constrained-by, provenance: asserted}
---

# The Bridge Is the Only Way In

## The rule (operator, 2026-09-23)

**Every Instagram read, and every Instagram write, goes through
instagram-bridge's HTTP API.** Nora's Python side has no other path to
Instagram: no OpenCLI row, no second session, no direct CDN or API call that
is not a URL the bridge handed back.

It closes the operator's objection from 2026-09-22 — *"we cannot have
everything scattered, with some things going through OpenCLI and others
through instameow"* — by construction rather than by discipline.

## What the rule restricts, and what it does not

It restricts **Nora**. It does not restrict **the bridge**. How the bridge
obtains a fact (a typed instameow method, a raw GraphQL document through
`MakeGraphQLRequest`, a raw REST call through `MakeRequest`, or a mechanism
not yet found) is the bridge's internal business, behind its gateway layer
(`16-bridge-architecture-and-observability.md`). That matters for the one
capability nothing serves yet: listing someone's posts or reels is now the
bridge's problem to solve, not a reason to keep a second door open.

## What this supersedes

- `07-social-boundary-and-scope.md`'s routing table sends Instagram `profile`
  and `posts` to OpenCLI. **Superseded.** Instagram has one row now.
- `08-social-adapter-architecture.md`'s default rule, *"Social reads go through
  the OpenCLI bridge. The Go package does messaging"*, with an exception list
  for Instagram. **Superseded for Instagram.** It still holds for X, which
  instameow cannot serve.

OpenCLI keeps X. It serves no Instagram verb.

## Status, measured 2026-09-23

With the bridge as the only door, a verb either has a bridge route that
works end to end or it does not. Nothing is counted as done because a
different backend could do it.

| Capability | Bridge route | End to end | Gap |
|---|---|---|---|
| Messaging: send, receive, react, typing, unsend, media | `/events`, `/send`, `/send-media`, `/react`, `/typing`, `/unsend` | ✅ live | routes are verb-shaped; see `16` §Resources |
| Room members | `GET /threads/{thread_fbid}/members` | ✅ live | — |
| Account profile incl. social fields | `GET /accounts/{igid}` | ⚠️ bridge ✅ (PR #387); Python still routes `profile` to OpenCLI | repoint Python |
| Story | `GET /accounts/{igid}/story` | ❌ **never reached**: the Python Instagram backend is not wired (`nora/app/composition.py` hard-codes it to `None`) because no `SocialSubjectIndex` implementation exists, so the router answers `unavailable`; and the client it would use targets `POST /social/story` with a `status` envelope the bridge never implemented | implement the subject index (its blocker, igids in `identity.toml`, is met since 2026-09-22); rewrite the client to the bridge's real contract |
| User search | none | ❌ Python routes `search` to OpenCLI | bridge route over `SearchUsers` |
| One post from a link | none | ❌ | bridge route over `FetchMedia`; never tried on a real post |
| List someone's posts | none | ❌ | **no known mechanism**: the Go library's full GraphQL registry (28 documents, all DM plus one profile query) has none; OpenCLI's `user` returns Instagram's HTML shell even from its own browser |
| List someone's reels | none | ❌ | same as posts |
| Followers / following | none | ❌ | not investigated |

## Links: how a pasted URL becomes a read

People ask about Instagram mostly by pasting a link, and a link names an
account by **username**. The bridge addresses accounts by **igid**, so every
link needs one conversion: **username → igid, done by the bridge**
(`GET /accounts?username=`, `16` §Resources).

Not from `identity.toml`: its `username` is display metadata, *read, never
matched*, because handles rename; and a link usually names someone who is not
in the file at all.

| Link | Becomes | Authorization |
|---|---|---|
| `instagram.com/<username>/` | username → igid → `GET /accounts/{igid}` | named (Tier 2): any account a person pasted |
| `instagram.com/p/<shortcode>/`, `/reel/<shortcode>/` | `GET /posts/{shortcode}` | named (Tier 2) |
| `instagram.com/stories/<username>/…` | username → igid → person (`Persons.resolve("instagram", igid)`) → `GET /accounts/{igid}/story` | **the room (Tier 0) only.** A story view appears in its author's viewer list; a link to a stranger's story is `refused`, never read |

The username lookup is a search, invisible to the account it finds, so it may
run before the Tier 0 decision; the story fetch, which is visible, runs only
after it.

Until the lookup route exists, a link answers `unavailable` (R-27), never a
guess and never OpenCLI.

## Resolution is not authorization

Two different questions, answered in two different places:

- **Resolution**: which igid is this? A `person_id` resolves through the
  person index (kind-aware: the index must return the `igid`, never the
  `fbid`, which is the same decimal-string trap `02` records). A username or
  link resolves through the bridge.
- **Authorization**: may Nora read it? Only `story` has a reach check (Tier
  0), because only a story read is visible to the person read. `profile` and
  `post` need resolution alone.

## Migration, in order

The concrete, PR-by-PR plan is `17-implementation-plan.md`.


**Cutover is per verb:** a verb's OpenCLI row is deleted in the same change
that points it at the bridge, so no verb ever has two answers and none loses
its answer before the replacement exists.

1. **Repoint what already works.** The Python Instagram backend calls
   `GET /accounts/{igid}` for `profile` and `GET /accounts/{igid}/story` for
   `story`; their OpenCLI rows go in the same change.
2. **Add the missing read routes** on the bridge, built to `16`'s
   conventions: username lookup (first, because links depend on it), search,
   one post by shortcode. Each verb's OpenCLI row goes when its bridge route
   lands.
3. **Posts and reels lists** get routes that return `unavailable` honestly,
   with the reason, so the verb exists and fails closed (R-27) while the
   mechanism is found.
4. **Writes** already go through the bridge. Their verb-shaped paths move to
   resource paths per `16`, with the Python adapter moving in the same change.

A verb that loses its OpenCLI answer before its bridge route exists says
`unavailable`. It never falls back to OpenCLI.
