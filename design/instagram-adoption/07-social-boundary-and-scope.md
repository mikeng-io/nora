---
type: design
title: "The Social Boundary — A Network Is Not A Platform, And Scope Is The Control"
description: "Instagram is the first social network Nora adopts rather than a fourth messenger. Two orthogonal ports joined by the person index, backends chosen below the port, and a four-tier reach ladder that bounds what Nora may look at."
tags: [nora, instagram, social, boundary, scope, architecture, ports]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 05-phase2-social-and-opencli.md, rel: supersedes-framing, provenance: asserted}
  - {dst: 06-retention-and-custody.md, rel: constrained-by, provenance: asserted}
  - {dst: ../10-identity-and-policy-schema.md, rel: builds-on, provenance: asserted}
---

# The Social Boundary

## The problem, stated precisely

Signal, Discord and WhatsApp are **messengers**. Everything Nora knows about a
person there arrives *through a room*: they said something, so she heard it.
`PlatformAdapter` encodes that faithfully — rooms, senders, messages,
mentions, replies, media — and three platforms have fitted it without strain.

Instagram is a **network**. The DM side fits `PlatformAdapter` exactly like
the others. But behind every sender there is now a profile, a story, a feed
and a follow graph that exist whether or not anyone said anything. That is a
second axis, and it is the first time Nora has had one.

The failure this document prevents is collapsing the two. If "Instagram" is
one thing, then the messaging adapter grows social verbs, the social fetcher
grows platform knowledge, and the choice between a Go library and a browser
CLI leaks upward until nobody can say what Nora is allowed to look at.

## Two ports, orthogonal, joined only by the person index

```
   MESSAGING AXIS                          SOCIAL AXIS
   PlatformAdapter                         SocialProvider
   ───────────────                         ────────────
   rooms, senders, messages,               profile, stories, posts,
   mentions, replies, media                search
        │                                       │
   Signal                                  Instagram ── instameow
   Discord                                           └─ opencli
   WhatsApp                                X ──────── opencli
   Instagram (DM)                          (Threads, …later)
        │                                       │
        └──────────► person index ◄─────────────┘
              interop fbid          igid
              (who spoke)           (whose profile)
```

**Instagram appears on both axes and is not one thing in the system.** It is a
platform on the left and a network on the right. They share an account and
nothing else: different ids, different failure modes, different retention
rules, different authority. `02-identity-and-policy.md` already records that
an Instagram person carries *two* identifiers; this is why. The left axis
produces `interop_messaging_user_fbid`, the right axis consumes `igid`, and
the person index is the only thing that knows they are the same human.

This also means **the social axis is not "Instagram support"**. It is a port
that X already lives on today through `social_fetch`, that Instagram now
joins, and that Threads or anything else joins later without touching the
messaging side.

## Backends live BELOW the port and Nora never learns there are two

The operator's concern — mixing up OpenCLI and the Go package — is exactly a
port violation. The fix is that backend selection is a routing table inside
the host-side bridge, per `(network, verb)`, and nothing above it knows:

| verb | Instagram | X |
|---|---|---|
| `story` | instameow (only path anywhere) | — |
| `profile` | opencli (social fields) | opencli |
| `posts` | opencli (instameow has no such call) | opencli |
| `search` | either | opencli |

> **CONTESTED 2026-09-22, pending ONE named probe.** The `posts` row's reason
> — "instameow has no such call" — is true of the client METHODS and false of
> the API. `web_profile_info` is declared in `messagix/endpoints/instagram.go`
> and its response is fully typed in `messagix/responses/profile.go`, carrying
> `is_private`, `followed_by_viewer`, `follows_viewer`, `id`, `fbid`,
> `biography`, the follower/following counts, AND
> `edge_owner_to_timeline_media` — the posts list itself. No client method
> calls it, but `Client.GetHTTP()` and `Client.GetEndpoint()` are exported, so
> the bridge can issue it with the DM SESSION'S OWN headers rather than a
> browser's.
>
> That matters because the OpenCLI half of these two rows does not currently
> work: `profile` and `following` have returned HTTP 429 on that same endpoint
> all day, from a browser-scraped request, while the account itself is fine in
> a real browser (05 §"Live state of the browser path"). Same endpoint, same
> account, different request shape.
>
> **The rows are NOT flipped here.** Whether Instagram answers the DM session
> where it refuses the scrape is unmeasured, and writing "instameow" in on a
> declared type would repeat the exact error the correction in `05` records —
> a capability inferred from a struct rather than read off a wire. The probe
> is one route, `GET /profile-social?igid=`, and its result flips these rows
> or restores them WITH the evidence attached.
>
> `story` and `search` are unaffected. X is permanently OpenCLI: instameow is
> Instagram-only, so no probe can change that row.

Measured facts behind those rows, so the table is not a preference:
instameow's entire social surface is `FetchReel`, `GetProfile`, `SearchUsers`,
`GetUserForNewDM` and `FetchMedia` — **no client METHOD lists a user's posts**
(an earlier revision said "no such call at all", a stronger and different
claim; see the note above), and `GetProfile` returns no follower, following or media counts and no
biography. OpenCLI has no story read; its `story` verb posts one.

Two consequences the operator should hold onto:

1. **OpenCLI cannot be dropped**, not for Instagram posts and profile fields,
   and not at all while X is wanted. The hoped-for prize — no VNC desktop, no
   headed Chrome — costs X *and* posts *and* profile fields together.
2. **Neither can instameow be avoided**, because stories exist on no other
   path, and stories are what Phase 2 was actually asked for.

The right reading is that this is one port with two backends, which is
ordinary, rather than two competing integrations, which is the mess.

## Scope is the control: four tiers of reach

Capability is not the interesting question — Nora can technically read a great
deal. **Reach** is. Every social read names a subject, and the subject sits in
exactly one tier:

| Tier | Subject | How identity is obtained | Default |
|---|---|---|---|
| **0 — the room** | someone in a room Nora is in | **derived**: `GetThread` → `Users[].ID` | **allowed** |
| **1 — the circle** | a known person in the workspace, not in this room | person index (`identity.toml`) | allowed, per-person flag |
| **2 — the named** | a handle someone in the room typed | explicit in the conversation | allowed, never resolved implicitly |
| **3 — the open network** | `explore`, `followers`, `following`, `saved` | none | **off** |

The ladder is the boundary, and its shape is deliberate:

- **Tier 0 is free because the subject opted in.** Someone who joined a group
  containing Nora is visible to her the way they are visible to everyone else
  in it. No configuration, no handle table — the room *is* the grant.
- **Tier 1 needs a per-person flag**, because being in the workspace is not
  the same as consenting to be looked up. `policy.toml` is room-shaped today
  and has no per-person capability; that gap is real and blocks Tier 1, not
  Tier 0.
- **Tier 2 requires the handle to have been typed by a person**, never
  inferred by the model from a name. **Only half of this is enforceable.**
  Whether a human typed a handle is a fact about the conversation that neither
  the tool nor the router can see; `target` reaches them straight from the
  model. What *is* enforceable, and is enforced, is that Nora never resolves a
  Tier 2 subject through the person index herself — asserted as a negative
  (the index is never consulted), because "we did not look anything up" leaves
  no trace in a status. Stated plainly so the unenforceable half is not
  mistaken for a control. This is the `web_search` screening shape
  (design/14 §2.2) applied to identity: the model may not invent a subject.
### `search` is Tier 2 on the QUERY, and that is a real gap

An earlier revision put "search results" at Tier 3 while the Tier 3 bullet
enumerated only `explore`/`followers`/`following`/`saved` — a contradiction,
and `x/search` and `instagram/search` are both in the live vocabulary today.

Resolved as **Tier 2 on the query**: a person typed the thing being searched
for, so the *act* is Tier 2. **But every account a search returns is a subject
Nora just read without any tier applying to it**, and nothing in the code
distinguishes the two. This is the one place the ladder is knowingly
incomplete. It is recorded rather than closed because narrowing it means
deciding what a search result *is* — a subject, or a pointer to one — and that
is an operator call, not an implementation detail.

- **Tier 3 is off by default** and is the only tier where Nora reads people
  who have no relationship to the conversation at all. `explore`,
  `followers`, `following` and `saved` all live here.

**Phase 2 is Tier 0 only.** That is what "first priority is reading the people
in that circle" means in this vocabulary, and it is the tier that needs no new
policy machinery at all.

## What this forbids, so that it is a boundary and not a diagram

- **No social verb on `PlatformAdapter`.** The messaging adapter never grows
  `get_story`. A room is not a lens on a profile.
- **No platform knowledge in the social port.** `SocialProvider` takes a
  network, a verb and a subject. Which library serves it is invisible above
  the bridge, exactly as DEC-151 already requires for its allowlist.
- **No implicit tier escalation.** A Tier 0 read may not follow a link into
  Tier 3. If Nora reads a story that shares a post, the post is a *new*
  subject at its own tier, not a continuation of the first read.
- **No scheduled social reads.** Every read is occasioned by the conversation.
  Nothing polls a profile, because a poll has no subject in the room and no
  reason at the moment it fires. This is the difference between an agent in a
  circle and a monitoring system pointed at people.
- **Social reads never persist** (`06-retention-and-custody.md`) — **with one
  known exception that is not yet closed**: the comprehension step
  (`media_fetch`) writes to the durable content-addressed cache, so the worked
  story flow persists a frame today. `10` §"The content" states the three
  candidate fixes and `11` scopes the decision out of M4, which means M4 would
  ship with this invariant known-broken unless it is gated on one. The tier
  ladder bounds *what* she may look at; retention bounds *what survives*.
  Both are needed: a Tier 0 read with durable custody would quietly build a
  dossier on a friend out of nothing but permitted acts.

## Resolution across platforms: the person index already does it

"Ask on Signal about someone's Instagram story" needs no new mechanism. The
person index is cross-platform by construction — one `[persons.<id>]` holds a
Signal block, a WhatsApp block and an Instagram block — so resolution is
always:

```
sender on ANY platform ──> person ──> that person's instagram.igid
```

The platform the question is asked from is irrelevant, which is the whole
point of the workspace/person model.

The prerequisite is that the Instagram block exists. Two paths:

- **Someone in an Instagram room with Nora**: free. They speak, the sender is
  unmapped, `nora persons pending` prints both ids, a human writes the block.
- **Someone who is not** (Tier 1): there is **no automatic path**. A human
  must obtain the numeric igid once — `SearchUsers` resolves a handle to one,
  measured working — and write it. The handle itself cannot be the key,
  because the index refuses renameable ids on every platform.

## The gap this design does NOT close: correlation

Raised by the operator, 2026-09-22, in its sharpest form: *"check Tyler's
Instagram story — where have they been today?"*, given that Nora also holds
presence analytics.

**The capability is real.** Story `taken_at` timestamps, Life360 presence,
room messages and the note tier compose into a timeline of where a person was
and what they were doing. Nothing in this bundle prevents it, and nothing
should be claimed otherwise.

**Every input is individually permitted.** Tyler is in the room, so he is Tier
0. Presence is already in Nora and predates this work. Story reads are
approved. There is no step at which a rule is broken — *the composition is the
dossier*, assembled entirely out of permitted acts.

**The three bounds in this bundle do not reach it:**

| bound | what it constrains | why it misses this |
|---|---|---|
| tier ladder (`07`) | *who* may be read | Tyler is Tier 0; every read is allowed |
| retention (`06`) | *what bytes survive* | the bytes are discarded; the **inference** is what persists |
| ports (`08`) | *how* a read is performed | correlation happens above all ports, in the model |

Retention protects the wrong thing here. A story frame vanishes in 24 hours;
"Tyler was at the beach at 3pm", written into a note, outlives it
indefinitely. This is `06`'s own warning — *"the comprehension is the durable
artefact, not the media"* — arriving as a concrete capability rather than a
caveat.

### The distinction that matters

The operator can authorize reads **of the operator**. Authorizing reads *and
correlation* **of Tyler** is different in kind, and Tyler is not in the
conversation where it is being decided. Tier 0's justification — *the subject
opted in by joining a room with her* — covers being heard in that room. It
does not obviously extend to having one's movements assembled from four
sources.

### What would bound it, if anything should

Stated as options rather than a decision, because this is the operator's to
make and it is about people, not code:

1. **Bound the derived claim, not the read.** The tier ladder currently
   governs reads; it could equally govern *claims about a person* — so
   "where was Tyler today" is a Tier-2 act requiring someone in the room to
   ask, while "did Tyler post a story" stays Tier 0.
2. **Bound persistence of derived claims.** Extend `06` from bytes to
   inferences: a location claim derived from a social read is as ephemeral as
   the read. This is the narrowest fix and the hardest to enforce, since the
   note tier is designed to persist.
3. **Bound the correlation itself** — presence and social may not be joined
   in one answer without an explicit ask. Crude, and probably the only one
   that is mechanically checkable.
4. **Accept it explicitly.** Nora is a member of the circle and members do
   know things about each other. Written down as a decision rather than left
   as an emergent property, so it is legible to the people it concerns.

**REVISED 2026-09-22 after external review (`12`): this GATES the story
capability rather than accompanying it.** Recording an open decision is
adequate only while the capability is off. The moment a Tier-0 story read
works, the dossier needs no new code — only a question — so an unresolved
decision attached to a live capability is not deferred, it is decided in
favour of the capability. The asymmetry that makes this plain: this bundle
demands a *recorded measurement* before admitting a backend exception, and
demanded nothing before admitting correlation.

**The original text, which understated it:** Nothing here is blocked on this. Phase 1 does not touch it and Phase 2
Tier 0 story reads do not require it to be settled. It is recorded now because
the capability arrives with Phase 2, and a capability that arrives before its
decision tends not to get one.

## The open decision this leaves

`policy.toml` has no per-person capability, and Tier 1 needs one. The shape is
not obvious: it could be a field on a person in `identity.toml`, a new
`[social]` block keyed by person, or a workspace-level default with
exceptions. Choosing it is Tier 1's blocker and only Tier 1's — Tier 0, which
is all Phase 2 was asked for, needs nothing new.
