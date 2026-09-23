---
type: design
title: "The Bridge Is the Only Way In — every Instagram read and write goes through instagram-bridge's HTTP API"
description: "Nora's Python side talks to Instagram through exactly one thing: instagram-bridge's HTTP API. OpenCLI leaves the Instagram rows entirely. How the bridge obtains a fact is the bridge's business; that the bridge is the only door is the rule."
tags: [nora, instagram, bridge, boundary, opencli, decision]
timestamp: '2026-09-24'
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
| List someone's posts | none | ❌ | **no known mechanism**: the Go library's full GraphQL registry (`pkg/messagix/graphql/docs.go`, 35 documents: 31 DM, 3 transport, 1 profile) has none; OpenCLI's `user` returns Instagram's HTML shell even from its own browser |
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
| `instagram.com/stories/<username>/…` | username → igid → person (`Persons.resolve("instagram", igid)`) → `GET /accounts/{igid}/story` | **the room (Tier 0) only.** Nora opens only listed people's stories (a privacy choice; see the 2026-09-24 correction below); a link to a stranger's story is `refused`, never read |

The username lookup is a search, invisible to the account it finds, so it may
run before the Tier 0 decision; the story fetch runs only after it.

**A link's reach check runs on the igid the link named, never a re-derived
one.** The forward path (`person_id` → `igid_for`) answers `ambiguous` for a
person with two Instagram accounts, correctly, because nothing says which one
was meant. A link *does* say: it named one account. So the link path goes
`igid → person` (reverse, `person_for_igid`) → `is_tier_zero(person)`, and reads
the story of the igid it already holds. Re-deriving through `igid_for` would
turn a multi-account friend's story link into `ambiguous`. An igid that maps to
no person is not in any room: `refused`.

Until the lookup route exists, a link answers `unavailable` (R-27), never a
guess and never OpenCLI.

> **2026-09-24: built on the Nora side (P10, P12).** The rows above are what
> `instagram_read` does with a `link` (and a typed `handle`), through one link
> parser, `nora/domain/instagram_links.py`, that the transcript render shares.
> A post or reel link is read only in a turn a person asked for, the same rule
> as a story (18). See 17 §P10, §P12.

## Resolution is not authorization

Two different questions, answered in two different places:

- **Resolution**: which igid is this? A `person_id` resolves through the
  person index (kind-aware: the index must return the `igid`, never the
  `fbid`, which is the same decimal-string trap `02` records). A username or
  link resolves through the bridge.
- **Authorization**: may Nora read it? Only `story` has a reach check (Tier
  0). `profile` and `post` need resolution alone.

  > **Reason corrected 2026-09-24 (P11 PR).** This line first said the
  > reason was that only a story read is visible to the person read. That was
  > never measured: whether the author sees the view is unmeasured (design
  > 04, 05 §Reading a story). The rule stands for a different
  > reason: Nora does not read the stories of people the operator has not
  > listed, as a privacy choice (`18` §"A story is opened only when a person
  > asks").

**Tier 0's scope (operator, 2026-09-23).** "In a room Nora is in" means a
person listed in the `persons` of at least one configured group room **on any
network**, not only Instagram rooms: Tier 0 is a property of the person, not
of where the question was asked. **DMs do not count**: Nora does not accept
DMs, and they are not on the roadmap, so `[dm] persons` never makes anyone
Tier 0. A `persons = "*"` room counts for nobody (`nora/infra/social/subjects.py`).

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
