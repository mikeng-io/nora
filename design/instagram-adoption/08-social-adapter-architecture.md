---
type: design
title: "Social Adapter Architecture — Topology, Hierarchy, and the Two Backends"
description: "The social adapter already exists as the SocialProvider port. What Instagram adds is a second implementation, a router, and one shared session. Full topology, call hierarchy, the security invariant restated for a non-OpenCLI backend, and the instameow/OpenCLI responsibility split per use case."
tags: [nora, instagram, social, architecture, topology, ports, instameow, opencli]
timestamp: '2026-09-21'
edges:
  - {dst: 07-social-boundary-and-scope.md, rel: parent, provenance: asserted}
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: ../09-decision-index.md, rel: constrained-by, provenance: asserted}
---

# Social Adapter Architecture

## The finding that shapes everything below: do not invent a port

Nora already has a social adapter. `nora.domain.ports.SocialProvider` is it,
and its docstring anticipates precisely this moment:

> "a PORT so another backend (a native Threads adapter, when OpenCLI grows
> one) plugs in with no tool change — and a second implementation means a
> shared contract suite both pass (`tests/contract/`)."

`OpenCliBridgeClient` is its only implementation today. **Instagram does not
need a new adapter kind; it needs to be the second implementation, plus a
router.** Introducing a parallel "social adapter" concept beside
`SocialProvider` is the mess the operator is trying to avoid, arriving by the
door marked *avoiding the mess*.

So the architectural work is three things, none of them a new abstraction:

1. a second `SocialProvider` implementation backed by instameow,
2. a router that dispatches per `(platform, what)` and is itself a
   `SocialProvider`,
3. a restatement of the security invariant so it survives a backend that is
   not a host-side subprocess.

## Topology

```
┌─ container: nora ───────────────────────────────────────────────┐
│                                                                 │
│  agent tools           social_fetch  (NativeTool, read_only)    │
│       │                     │  closed vocabulary (platform,what)│
│       ▼                     ▼                                   │
│  domain port          SocialProvider                            │
│                             │                                   │
│                    SocialRouter (SocialProvider)                │
│                       │              │                          │
│        OpenCliBridgeClient      InstagramSocialClient           │
│                       │              │                          │
└───────────────────────┼──────────────┼──────────────────────────┘
                        │ loopback     │ private `nora` network
                        ▼              ▼
        ┌─ host process ──────┐   ┌─ container: instagram-bridge ─┐
        │  SocialBridge       │   │  Go, instameow                │
        │  READ_ONLY_ALLOWLIST│   │  READ_ONLY_ALLOWLIST (its own)│
        │  spawns opencli     │   │  ONE authenticated session    │
        └──────────┬──────────┘   └───────┬───────────────┬───────┘
                   ▼                      │               │
          headed Chrome / VNC             │               │
          (X + IG profile/posts)   social reads     messaging (SSE/HTTP)
                                   FetchReel etc.         │
                                                          ▼
                                                  PlatformAdapter
                                                  (nora/platform/instagram/)
```

Three processes, two seams, and one container that appears on both axes.

## Hierarchy: who may call whom

Strictly downward, and each arrow crosses exactly one trust boundary:

| Layer | May call | May NOT call |
|---|---|---|
| `social_fetch` tool | `SocialProvider` | any bridge directly; any backend by name |
| `SocialRouter` | either `SocialProvider` implementation | Instagram's messaging surface |
| `OpenCliBridgeClient` | host `SocialBridge` over loopback | instameow, the instagram-bridge |
| `InstagramSocialClient` | instagram-bridge social routes | the host bridge, OpenCLI |
| `PlatformAdapter` (IG messaging) | instagram-bridge messaging routes | anything on the social axis |

**The model never names a backend.** It names `(platform, what, target)`; the
router decides. That is what keeps "OpenCLI plus the Go package" from becoming
two integrations the model has to choose between — a choice it would make
badly and inconsistently, and which would leak the topology into the prompt.

## The one place the two axes touch: the session, not the port

The instagram-bridge container serves **both** axes. That is deliberate and it
is the only coupling in this design:

- It holds **one** authenticated Instagram session. Messaging and social reads
  share it.
- A second session for social reads would mean a second cookie jar, a second
  MQTT connection, and two concurrent logins for one account — the
  configuration most likely to provoke a checkpoint
  (`01-bridge-container.md`).

They share a **session**, not a port. The bridge exposes two disjoint route
groups (`/events`, `/send`, … for messaging; `/social/...` for reads), and
nothing in Nora crosses between them. The `PlatformAdapter` cannot fetch a
story; the `SocialProvider` cannot send a message. The shared session is an
implementation fact inside one process, invisible above it.

A useful consequence: **when the session is checkpointed, both axes degrade
together and for the same reason** — one health signal, one re-auth, one
operator action.

## The security invariant, restated so it survives a second backend

DEC-151 and `SocialProvider`'s docstring both say the allowlist lives
**host-side**. Read literally, an in-container backend violates that. Read for
its reason, it does not.

The reason is stated in the decision itself: *the tool runs in the container
beside a model that can be prompt-injected by the very content it reads*, so
enforcement must happen somewhere that content cannot reach. The property that
matters is **"a process the model cannot inject"**, and "the host" was how
that was achieved when there was one backend, not the point of it.

The invariant, restated:

> Every social read is re-authorized against a frozen `(network, verb)`
> allowlist **in the process that performs it**, which is never the process
> running the model. A request outside the allowlist is refused without
> performing any read.

That keeps R-86's three layers intact with two backends:

1. the tool's closed vocabulary — unchanged,
2. **each backend's own allowlist**, in its own process — the host bridge
   keeps its `(site, command)` map; the instagram-bridge gets an equivalent
   one covering only its read verbs,
3. `NativeTool read_only=True` — unchanged.

R-86's test pins the three layers against each other. With two backends it
must pin **both** allowlists, and the `SocialProvider` contract suite the port
docstring already calls for is where the two implementations are held to the
same behaviour.

## The routing rule: the axis split is the DEFAULT, not a tie-break

> **SUPERSEDED FOR INSTAGRAM 2026-09-23 by `15-bridge-is-the-only-entry.md`.**
> Every Instagram verb goes through instagram-bridge's HTTP API; OpenCLI serves
> no Instagram row. The table and notes below are kept as the record of how
> the split was reasoned and measured. They are not the current routing. X is
> unaffected.

The operator's boundary is the rule, and it is stated first because everything
below is an exception to it:

> **Social reads go through the OpenCLI bridge. The Go package does messaging.**

`SocialRouter` exists to serve a short, justified **exception list**, not to
pick a favourite backend per verb. A row belongs on that list only when:

1. OpenCLI **structurally cannot** serve it — not "serves it worse", not
   "is flaky today", but has no such command; and
2. the capability is actually wanted by a named use case; and
3. the exception is recorded here with the measurement that established (1).

**The list had one row (`instagram/story`) when this section was written; it
has three as of the 2026-09-22 correction below: story, profile (`GET
/accounts/{igid}`, folding in `is_private`/counts/`followed_by`), and
post/reel counts.** OpenCLI 1.8.7's `instagram` surface marks `story` as
`[write]` — it posts a story — and has no story read anywhere. `FetchReel`
is the only path that exists for that row; the other two are the
`08`/`07`/`05` profile correction, which reads on test (1) as OpenCLI's
`web_profile_info` being sustainedly throttled (five-plus hours, both
request shapes) while the GraphQL document instameow already used for
messaging answers the same session normally — not "flaky today", a
persistent refusal of that specific endpoint.

One thing this rule still deliberately excludes, though instameow can do it:

- **`latest_reel_media`** as a cheap "do they have a story up" check — an
  optimisation of a row that is already routed. Fails test (1): it is not a
  capability, it is a faster way to do one.

(`friendship_status` — does this account follow them, are we blocked — was
previously excluded here on the same "no use case has asked for it" ground.
It is now routed as part of the profile row above: `following` and
`followed_by` are both in the same response the operator's gate already
needed, so carrying them cost nothing additional once that call existed.)

The rule matters more than the row. A router whose entries are chosen by
"which backend is better here" drifts into two parallel integrations within a
year, because every verb has a better backend on some axis. A router whose
entries each carry a proof of impossibility cannot drift: the list shrinks if
OpenCLI grows a verb, and grows only against evidence.

### Why the exception cannot be closed upstream

OpenCLI is **`@jackwener/opencli`**, a third-party npm package (1.8.7,
installed under `~/.hermes/node`). It is not ours to extend, so "add a story
read to OpenCLI" is not an available move and the exception is not a temporary
workaround awaiting a patch we control. If upstream ever ships a story read,
test (1) fails and the row is removed — that is the only way the list
shrinks.

## What actually protects the bridge's write verbs today — measured 2026-09-22

The bridge serves `/send`, `/react` and `/unsend` **unauthenticated, with no
allowlist**, unlike the OpenCLI host bridge. What stops a prompt-injected
model reaching them is network isolation, and that had been *derived from
Docker defaults* rather than measured. Now measured on norty:

| probe | result |
|---|---|
| a `nora`-only service by DNS from `nora-compute` | `Could not resolve host` |
| the same service by its `nora`-network IP | connection timed out |
| **positive control** — the host social bridge on `172.17.0.1:19826` | `http=200` |

The control is what makes the two failures mean something: without it, two
timeouts are indistinguishable from a broken probe.

**So isolation holds, and it is one layer.** Three things follow:

1. **The reachable path is the OpenCLI bridge, and there the design works.**
   `make_artifact` → the Smith sandbox → `run_python` → arbitrary sockets
   reaches `172.17.0.1:19826` unauthenticated. `READ_ONLY_ALLOWLIST` refuses
   every pair outside its seven read rows and spawns nothing, so a write verb
   is unconstructible *with no help from topology*. That is precisely the
   property DEC-151 claims for it, now exercised rather than asserted.
2. **The Instagram bridge has no such property.** Its safety is entirely
   topological, and R-86 exists because one layer was judged insufficient.
   It needs its own frozen allowlist **before `/social/*` is written**, not
   after — at which point the route also becomes the thing the allowlist
   names, rather than a tautology over a single route.
3. **The isolation is fragile in a specific way.** `drop` is deliberately
   dual-homed (`nora-compute nora_nora`) so Smith can reach it. Any future
   need to let Smith reach the Instagram bridge would attach it the same way
   and silently delete the only control protecting its write verbs. Recorded
   so that change cannot be made casually.

## Responsibilities: instameow vs OpenCLI, per use case

Measured, not preferred. instameow's complete social surface is `FetchReel`,
`GetProfile`, `SearchUsers`, `GetUserForNewDM`, `FetchMedia` — **there is no
list-a-user's-posts METHOD** (the endpoint exists and returns the timeline —
see 07's contested-rows note, 2026-09-22). OpenCLI has no story *read*; its `story` verb
posts one.

| Use case | Backend | Why |
|---|---|---|
| "what's on X's story right now" | **instameow** — an exception | `FetchReel(igid)`. OpenCLI has no story read; its `story` verb posts one. |
| "show me their profile — bio, follower count" | **instameow** — an exception (reversed 2026-09-22) | `PolarisProfilePageContentQuery`, issued raw, keyed on the numeric igid. See the correction below. |
| "list this person's recent posts" | OpenCLI | default; instameow has a COUNT (`media_count`, `total_clips_count`) but still no listing call |
| "read this specific post someone linked" | OpenCLI | default. `instagram/post` is `unavailable` on both paths today; `FetchMedia` returned HTTP 400 on the one id tried, so it is not a candidate exception until proven |
| "search for a user by name" | OpenCLI | default; the two are equivalent, so there is no case for an exception |
| anything on **X** | OpenCLI | default; instameow is Instagram-only |
| "does this person follow us" | **instameow** — an exception (reversed 2026-09-22) | `friendship_status.followed_by` on the same profile call above |
| Threads, later | neither yet | joins as a further backend behind the same port |

> **CORRECTION 2026-09-22.** The profile and follow-back rows above were
> written when `GetProfile`'s TYPED response was the only measurement:
> `slidetypes.ProfilePageResponse` carries `user`/`viewer` only, so its
> unmodelled ~50 fields never reached this document as a possibility. A
> second call to the same GraphQL document (`PolarisProfilePageContentQuery`,
> not `web_profile_info` — that REST endpoint is genuinely and separately
> throttled), issued raw and keyed on the numeric igid, returns `data.user`
> whole: 65 keys, including `is_private`, `follower_count`, `following_count`,
> `mutual_followers_count`, `media_count`, `total_clips_count`, `biography`,
> `is_business`, `is_professional_account`, `followed_by`. Full detail and
> the two implementation mistakes it took two more rounds to find (a
> username sent into a numeric-id field; a GraphQL `errors` envelope read as
> success because only the HTTP status was checked) are in `05`'s and `07`'s
> matching corrections and `deploy/instagram-bridge/social_profile.go`.
>
> **"List this person's recent posts" is UNCHANGED by this correction** —
> `media_count` is a count, not a feed, and no instameow client method pages
> a timeline. That row, and the structural conclusion about OpenCLI below,
> stand for posts-as-a-list specifically.

Two structural conclusions the operator asked for directly, one of them now
narrower than when it was written:

- **OpenCLI cannot be dropped**, but its Instagram job shrank to
  posts-as-a-list only (plus `instagram/post`, unproven either way). Not
  "Instagram posts or profile fields" any more — profile fields moved to
  instameow above. OpenCLI is still not droppable at all while X is wanted.
- **instameow cannot be avoided**, and its social footprint is now three
  exceptions — story, profile (incl. `is_private` and the follow-back
  fact), and post/reel counts — not one. Everything that remains on the
  default side of the boundary is genuinely absent from instameow's client
  surface: a posts LIST, and `instagram/post` by permalink.

The overlap is therefore almost nil by construction rather than by luck: the
exception list admits only what the default backend cannot do, so there is
nothing to arbitrate and no duplicated capability to keep in sync.

## Failure semantics differ per backend, and the router must not hide it

| | OpenCLI path | instameow path |
|---|---|---|
| typical failure | browser cannot navigate; HTML instead of JSON | session checkpointed |
| blast radius | X **and** Instagram profile/posts | Instagram social **and** Instagram messaging |
| recovery | restart Chrome / re-login in the VNC session | re-auth the bridge session |
| observed | `Pre-navigation … Navigation rejected` (2026-09-21) | none yet; soak running |

Both map to R-27's `unavailable`, never to `nothing_found` — an error is never
a pass. But the router must **not** fail over between them: if
`instagram/story` is unavailable because the session is checkpointed, the
answer is `unavailable`, not a silent substitution with a profile read. A
router that improvises is a router that lies about what it looked at.

## What concretely changes

Nothing above the port. Specifically:

1. `social_fetch`'s vocabulary gains `story` (one row); the tool is otherwise
   untouched. The host `READ_ONLY_ALLOWLIST` keeps its existing 7 rows
   (`x` × post/profile/posts/search, `instagram` × profile/posts/search)
   unchanged — `story` is the only pair that does not appear there, which is
   exactly why it is the exception.
2. `SocialRouter` is added as a `SocialProvider` and composed in
   `build_dependencies` where `OpenCliBridgeClient` is today.
3. `InstagramSocialClient` is added as the second `SocialProvider`.
4. The instagram-bridge grows `/social/*` routes and its own frozen allowlist.
5. `tests/contract/` gains the shared `SocialProvider` suite the port's
   docstring has been asking for since it had one implementation.
6. `READ_ONLY_ALLOWLIST` (host) is unchanged — it keeps X and the Instagram
   verbs OpenCLI still serves.

## What must not happen

- **No social verb on `PlatformAdapter`**, and no messaging verb on
  `SocialProvider`. The shared session does not license a shared interface.
- **No backend name in the model's vocabulary.** If a prompt ever says
  "instameow" or "opencli", the boundary has already failed.
- **No failover between backends.** See above.
- **No exception without a proof.** A verb moves to instameow only against a
  recorded measurement that OpenCLI cannot serve it. "Better", "faster" and
  "working today" are not proofs.
- **No second Instagram session.** The bridge owns exactly one.
- **No social read without a subject in a permitted tier**
  (`07-social-boundary-and-scope.md`). Architecture bounds *how*; the tier
  ladder bounds *who*; retention (`06`) bounds *what survives*. All three are
  required, and none substitutes for another.
