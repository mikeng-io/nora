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
   PlatformAdapter                         SocialSource
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

Measured facts behind those rows, so the table is not a preference:
instameow's entire social surface is `FetchReel`, `GetProfile`, `SearchUsers`,
`GetUserForNewDM` and `FetchMedia` — **there is no list-a-user's-posts call at
all**, and `GetProfile` returns no follower, following or media counts and no
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
| **3 — the open network** | explore, search results, arbitrary URLs | none | **off** |

The ladder is the boundary, and its shape is deliberate:

- **Tier 0 is free because the subject opted in.** Someone who joined a group
  containing Nora is visible to her the way they are visible to everyone else
  in it. No configuration, no handle table — the room *is* the grant.
- **Tier 1 needs a per-person flag**, because being in the workspace is not
  the same as consenting to be looked up. `policy.toml` is room-shaped today
  and has no per-person capability; that gap is real and blocks Tier 1, not
  Tier 0.
- **Tier 2 requires the handle to have been typed by a person**, never
  inferred by the model from a name. This is the `web_search` screening shape
  (design/14 §2.2) applied to identity: the model may not invent a subject.
- **Tier 3 is off by default** and is the only tier where Nora reads people
  who have no relationship to the conversation at all. `explore`,
  `followers`, `following` and `saved` all live here.

**Phase 2 is Tier 0 only.** That is what "first priority is reading the people
in that circle" means in this vocabulary, and it is the tier that needs no new
policy machinery at all.

## What this forbids, so that it is a boundary and not a diagram

- **No social verb on `PlatformAdapter`.** The messaging adapter never grows
  `get_story`. A room is not a lens on a profile.
- **No platform knowledge in the social port.** `SocialSource` takes a
  network, a verb and a subject. Which library serves it is invisible above
  the bridge, exactly as DEC-151 already requires for its allowlist.
- **No implicit tier escalation.** A Tier 0 read may not follow a link into
  Tier 3. If Nora reads a story that shares a post, the post is a *new*
  subject at its own tier, not a continuation of the first read.
- **No scheduled social reads.** Every read is occasioned by the conversation.
  Nothing polls a profile, because a poll has no subject in the room and no
  reason at the moment it fires. This is the difference between an agent in a
  circle and a monitoring system pointed at people.
- **Social reads never persist** (`06-retention-and-custody.md`). The tier
  ladder bounds *what* she may look at; retention bounds *what survives*.
  Both are needed: a Tier 0 read with durable custody would quietly build a
  dossier on a friend out of nothing but permitted acts.

## The open decision this leaves

`policy.toml` has no per-person capability, and Tier 1 needs one. The shape is
not obvious: it could be a field on a person in `identity.toml`, a new
`[social]` block keyed by person, or a workspace-level default with
exceptions. Choosing it is Tier 1's blocker and only Tier 1's — Tier 0, which
is all Phase 2 was asked for, needs nothing new.
