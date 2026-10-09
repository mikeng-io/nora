---
type: design
title: "Architecture: the summons occasion, the threads-bridge, and the two axes kept apart"
description: "Topology, the SocialSurface port, how a summons is comprehended into a model with ages, the public tool grant, the staging deliverer, image hosting, token custody and refresh, and what each existing guard does on a public surface."
tags: [nora, threads, architecture, social-surface, deliverer, bridge, token, drop]
timestamp: 2026-09-25
status: parked
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
  - {dst: ../instagram-adoption/08-social-adapter-architecture.md, rel: extends, provenance: asserted}
  - {dst: ../instagram-adoption/16-bridge-architecture-and-observability.md, rel: follows, provenance: asserted}
---

# Architecture

## Topology

```
┌─ container: threads-bridge (Python, deploy/threads-bridge/) ─────────────┐
│  holds THE Threads token (nora never does)                                │
│  token ledger + daily refresh           summons ledger (id → state)       │
│  READ allowlist:  mentions, post, conversation, replies, profile          │
│  WRITE allowlist: reply(summons_id, text, image_url)  — nothing else      │
└────────────┬──────────────────────────────────────┬──────────────────────┘
             │ private `nora` network               │ HTTPS
             │                                      ▼
             │                               graph.threads.net
┌─ container: nora ─────────────────────────────────────────────────────────┐
│                                                                           │
│  SummonsPoller ──► ThreadsSummonsSource (SocialSurface) ──► bridge        │
│        │                                                                  │
│        ▼                                                                  │
│  SummonsOccasion ── SummonsContext (comprehended once, ages rendered)     │
│        │                                                                  │
│        ├─ ToolRegistry.surface(ToolScope(platform="threads",              │
│        │                                 grant=PUBLIC_GRANT))             │
│        ├─ doctrine base + public.md                                       │
│        ├─ LiveAgentRunner (same model loop, same Langfuse tracing)        │
│        └─ ThreadsDeliverer  ── stages text/images, publishes ONCE ──► bridge
│                                                                           │
│  threads_read (SocialProvider row) ─► SocialRouter ─► bridge (READ only)  │
└───────────────────────────────────────────────────────────────────────────┘
                              drop (public listener) ◄── image bytes, short TTL
```

## Two axes, kept apart

| Axis | Port | Direction | Serves |
|---|---|---|---|
| read | `SocialProvider` row `threads/*` | Nora reads Threads **for a room** | a member pastes a Threads link in Signal; `threads_read` answers in the room |
| surface | new `SocialSurface` | Nora is summoned **on** Threads, and answers **there** | the summons occasion |

They share a **session**, the one token in `threads-bridge`. They do not share
a port. This is the same split design/instagram-adoption/08 made for the
instagram-bridge, which serves both messaging and reads from one session.
Nothing on the read axis can publish, and the surface axis never feeds a
room.

## The bridge: why a sidecar, not an HTTP client inside `nora`

The Threads Graph API is plain HTTPS with a bearer token. An in-process
`aiohttp` client would work. It is rejected for one reason: **the token that
can post publicly as Nora must not live in the process that reads
prompt-injected content.** The restated R-86 invariant (08 §"The security
invariant") is that every social verb is re-authorized against a frozen
allowlist in the process that performs it, which is never the process
running the model. A sidecar keeps that invariant, and on the write side it
matters more than on the read side.

`threads-bridge` is small Python (aiohttp), built like `deploy/drop/` and
`deploy/social-bridge/`: it reads no `nora.config`, only env vars, and has a
`/health`. It is reachable only on the private compose network. It exposes:

| Route | Graph call | Notes |
|---|---|---|
| `GET /summonses?since=` | `GET /{uid}/mentions` | records each mention in its summons ledger as `seen` |
| `GET /posts/{id}` | `GET /{id}?fields=…` | one post; read allowlist |
| `GET /posts/{id}/chain` | walks `replied_to` up to `root_post`, then `GET /{id}/conversation` if asked | read allowlist |
| `POST /summonses/{id}/reply` | `POST /{uid}/threads` (`reply_to_id={id}`), poll status, `POST /{uid}/threads_publish` | **the only write** |
| `GET /health` | `GET /me?fields=id,username` + token age | |

`POST /summonses/{id}/reply` is refused unless `{id}` is in the ledger as a
summons this bridge served and has not yet been answered, and `text` is at
most 500 characters. The ledger transition `claimed → published` is one
Postgres row update keyed on the summons id, so a retry after a timeout
reads the recorded reply id and does not publish again. The model never sees
a Graph id. It can name neither a post to reply to nor an account to post to.

## Ingress: polling, and why not webhooks yet

`SummonsPoller` is a background task in `nora`, built like
`nora/app/compute_poller.py`. Every `poll_seconds` (default 30) it asks the
bridge for summonses since the last watermark and hands each new one to the
occasion.

Webhooks (topic `mentions`) would cut latency. They need a public HTTPS
callback with `X-Hub-Signature-256` verification, and **nothing in this stack
receives inbound internet traffic to Nora today.** `nora/web/api.py` is GET
only, on :9100, internal. The one public listener, `drop`, deliberately
carries no write route (design §5a of Nora Drop). A webhook route would be
Nora's first public inbound endpoint, a bigger decision than this feature
needs. Polling at 30 s puts a summons about 15 s behind on average, which is
within what a person tolerates for a researched public answer. **This holds
unless** summons latency is the measured complaint, or Meta's mention polling
turns out to lag webhooks by minutes (measure it in phase 1).

Meta also delivers mentions only from **public** accounts. A summons from a
private account never arrives. That is Meta's rule, and the doctrine does not
need to know about it.

## Who may summon

The bridge serves every mention. `nora` filters. The occasion resolves the
summoner through `Persons` against `[[persons.<id>.threads]]` blocks. A
summoner who resolves to no person is recorded as `ignored_summoner` and
gets no answer. Posting a refusal in public to a stranger is worse than
saying nothing.

**The API exposes no author id, only a username** (measured 2026-09-25 on
the first real mention): `/mentions` returns `username`, and `owner` is
absent even when asked for, on the mention and on its root post.
`profile_lookup` needs `threads_profile_discovery`, which the OAuth flow did
not grant. So the block is matched on the username. design/10 §1.3 says a
name is spoofable and a platform id is not, and a username is spoofable in
exactly one way: someone renames, releases the handle, and a stranger claims
it.

That gap is closed today by something other than Nora. Until App Review,
Meta delivers mentions **only from accounts that hold a tester role on the
app**, and the tester role is held by account, not by handle. A stranger who
claims a friend's released handle is not a tester, so their mention never
arrives. The two gates together are the rule: Meta's tester list binds the
account, and `identity.toml` binds the person.

**Before App Review is ever passed**, the first gate disappears and the
username has to be pinned to an account. The candidate: a Threads username
is its Instagram account's username, so the instagram-bridge's exact-match
account lookup (`GET /accounts?query=`, measured exact in instagram-adoption
P9) can resolve it to an `igid`, which the block then stores and compares.
That is unmeasured. It couples the check to the Instagram session's health,
and it has to fail closed. It is not needed while the app is unreviewed.

The Threads account need not be the same one the person uses on Instagram
elsewhere in `identity.toml`. The operator's Threads account is `mikeng_io`,
and his `instagram` block holds a different account. So a summoner's
`threads` block is its own entry, not derived from `instagram`.

Resolving the summoner to a person does **not** open that person's private
context to the occasion. The summons still runs with no memory and no room
(decision 1), because the audience is public even when the summoner is a
friend. Being in `identity.toml` decides who can make her speak, not what
she may say.

## What the first mention showed (2026-09-25)

| Question | Answer |
|---|---|
| does a tester's mention arrive in `/mentions` | yes, once the account accepted the tester invite; not before |
| fields on the mention | `id`, `text`, `username`, `timestamp`, `media_type`, `is_reply`, `root_post.id`, `replied_to.id`, `permalink`; no `owner` |
| can the root post be read | yes, when a tester wrote it (text, author username, timestamp, permalink) |
| **a summons under a non-tester's post** | the mention arrives with `is_reply: true`, but **`root_post` and `replied_to` are absent**, and `/{summons}/conversation` answers `{"data":[]}`. The post she is asked about is invisible. Measured on a reply that was still up and readable (`GET /{id}` 200, `hide_status: NOT_HUSHED`). A first probe was withdrawn because its reply was deleted around the read |
| the public web page of that summons | a JavaScript shell (282 kB) with neither the summons text nor the parent in it; no server-rendered fallback |
| `/{root}/conversation` | yes, the reply tree with `replied_to` on each item |
| a quote post | `is_quote_post: true`, but `quoted_post` did not come back when requested. **What was quoted is invisible so far**, and a claim is often in the quoted post |

## Comprehend: the `SummonsContext`

Built once, from the bridge's `chain`, before the model runs. Everything
after computes on it.

```
SummonsContext
  summons:   id, author handle, text (handle stripped), posted_at, age
  question:  the summons text minus the handle; EMPTY means "the post above"
  chain:     root → … → parent, each: author handle, text, media kind,
             media handles (for media_fetch), posted_at, age, is_root
  truncated: how many chain posts were elided (a long thread is clipped
             from the middle, keeping the root and the three nearest)
  now:       the clock the ages were computed against
```

The CMS lesson from the Relevance Gate applies here: the model will be asked
whether a claim **is still** true. So every post carries its age, and the
block states which end is newest. "Is this true?" about a post from 2023 is a
different question from the same words about a post from this morning, and
the input has to let the model tell those apart.

The chain is rendered as **untrusted content** (the `social_post` wrapper
the read tools already use, `nora/infra/social/render.py`). A post under which
she is summoned is exactly where an injection will be written.

Images in the chain are not comprehended at ingest. Each carries a media
handle the model may pass to `media_fetch`. This follows the operator rule
from #435: shared content is read when it is asked about.

## The occasion

`SummonsOccasion` is the summons analogue of the engagement pipeline's
invoke stage, and much smaller:

1. claim the summons in the ledger, or return if already claimed,
2. resolve the summoner to a person in `identity.toml`, or stop,
3. build `SummonsContext`,
4. build the surface: `ToolScope(platform="threads", grant=PUBLIC_GRANT)`,
5. build the prompt: doctrine base + `public.md` + the context block,
6. run `LiveAgentRunner` with a `ThreadsDeliverer`,
7. flush the deliverer: one publish, or none,
8. record the outcome row (`published`, `silent`, `refused`, `failed`,
   `ignored_summoner`) with the Langfuse trace id.

What it does **not** construct is the point. There is no `RoomView`, no
roster, no prefill blocks (transcript, peer card, standing, notes), no
Honcho write, no audit row in any room's store, no decision-log write and no
debrief. Whether `LiveAgentRunner.invoke`'s `TagContext` can be built with
those empty, or whether the runner needs a narrower entry point, is the
first thing the implementation checks (20 §"Phase 2").

### `PUBLIC_GRANT`

| admitted | why |
|---|---|
| `send_reply` | the one way she speaks; terminal |
| `web_search`, `web_fetch` | the fact check |
| `media_fetch` | the chain's own images, by handle |
| `generate_image` | "draw this" summonses |
| `threads_read` | read a post linked in the chain |
| `x_read`, `x_search` | claims are often about X posts; public data |
| `now` | the ages are relative to it |

Everything else is refused by the scope, including memory, recall, notes,
reminders, `where_is`, presence, persons, `group_members`, `send_to`,
reactions, GIFs, music, `instagram_read` (its story reads are
roster-scoped), and `linkedin_*` (a profile visit its subject can see).
`ToolScope.grant` already exists and already works this way for the
Dreamer (`nora/agent/surface.py`). The grant is a frozen set in code, pinned
by a test that fails if a room-scoped tool, or a tool with
`read_only=False` other than `send_reply` and `generate_image`, enters it.

## Egress: the `ThreadsDeliverer`

`send_reply` and `generate_image` both call `deliverer.deliver(...)`
(`nora/agent/tools.py`, around lines 7043 and 10719), and the deliverer
already has a held/sent distinction. On a summons the deliverer:

- **stages** instead of sending: text from `send_reply`, image bytes from
  `generate_image`,
- **refuses** text over 500 characters with a `note` that asks her to
  shorten it, so she rewrites. It never truncates silently. A cut-off
  fact check is a wrong fact check,
- **refuses** a second `send_reply` text: one reply per summons,
- **flushes once**, when the turn ends, as one `POST /summonses/{id}/reply`:
  text only, text + one image (`media_type=IMAGE`), or image only.

Strict silence holds: no `send_reply` means no text. A staged image with no
text is still published, because `generate_image` delivering is her act in a
room today, and the summons keeps that meaning.

The R-18 identifier screen runs on the staged text as it does on every
reply. Threads renders no markdown, so the staged text goes through
`strip_markdown` (`nora/agent/markdown.py`) before it is measured against
the 500-character limit, so the limit is checked on the text that will
actually be posted.

## Images need a public URL

The Threads API takes `image_url`, a URL Meta fetches. It does not accept an
upload. `generate_image` returns bytes (`xai_adapter.py` asks for
`b64_json`), so the flush has to host them first. **Drop was retired 2026-10-08 (ADR 0023), so this candidate and its two
conditions below are history; image hosting for phase 4 needs a new host.**
The candidate was `drop`:
`POST /api/artifacts` on its internal listener already stores bytes with an
`expires_at` and returns a public slug. Two things stand in the way, and
both are measured before phase 4:

1. `drop`'s public listener already serves `/drop/{slug}` as the stored
   bytes with their stored media type (`deploy/drop/server.py`,
   `render_artifact`, deleted 2026-10-08), so no new route is needed for the bytes. What is
   needed is that the served URL stops working at `expires_at`.
2. `drop`'s public listener **rejects every bot user agent** (an operator
   requirement, `deploy/drop/hardening.py`, deleted 2026-10-08). Meta fetches `image_url` with a
   crawler user agent that has not been measured. Exempting it on `/m/` only
   narrows that operator rule, so it is the operator's decision (20 §"Needs
   the operator").

The expiry is short (15 minutes). Meta copies the image at container
creation, and the published post does not depend on the URL afterwards.
That claim gets measured too: publish, let the URL expire, then read the
post back.

## Token custody and refresh

A long-lived Threads token lasts 60 days. It can be refreshed once it is at
least 24 hours old. Once it expires it cannot be refreshed, and the operator
has to run the OAuth flow again. A value in `.env` cannot be rewritten by a
container, so:

- `threads-bridge` seeds a one-row `threads_token` table from
  `THREAD_API_AUTH_TOKEN` (the variable name the re-minted token uses; the
  bridge reads it from the deploy host's `.env`, so it has to be copied to
  norty first) on first start and reads the table from then on,
- it refreshes daily once the token is older than 24 hours, and records
  `refreshed_at` and `expires_at`,
- `/health` reports `days_to_expiry`, and an alert fires under 14 days or on
  any refresh failure. The alert reads a gauge, not a counter: `nora`
  restarts several times a day, and a counter for an event that happens
  once a day is empty most of the time.

## What each existing guard does here

| Guard | On a summons |
|---|---|
| R-5, no error text reaches the audience | unchanged: a failed summons publishes nothing, never an error |
| grounding (the spine: never assert what you cannot ground) | stronger: publishing nothing is always allowed, and the doctrine says so |
| R-18, no identifier egress | unchanged; the screen runs on staged text |
| R-21, room-scoped name resolution | unreachable: no room tool is granted |
| R-28, no name or coordinate in routing arguments | unchanged on `web_search`; no routing tool is granted |
| the disclosure subset (`AGENTS.md`'s R-31, no index row) | unreachable: nothing private is in context or in reach |
| R-86, read-only social | unchanged on the read axis; the write axis gets its own allowlist in the bridge |
| strict silence | unchanged; the deliverer publishes only what the tools staged |

The design's safety rests on the second column being "unreachable" rather
than "patched". One test pins it: every tool in `PUBLIC_GRANT` is either
`read_only` or is `send_reply` or `generate_image`, and none of them reads
Honcho, rosters or the persons file.

## Doctrine

A new file, nora/doctrine/public.md, in English like `conduct.md` (it describes
behaviour; `voice.md` is Cantonese because it is a specimen of her register):

- The audience is strangers, and the answer is permanent.
- A verdict is grounded or withheld. A claim she could not check is said to
  be unchecked. She cites where the check came from, as a link when one fits
  in 500 characters.
- She answers in the language of the summons.
- She never says anything about the summoner or anyone else beyond what is
  in the thread.
- An image is generated only when asked for.

Which base doctrine files are loaded with it is an explicit list in code, not
"all of them". A file that carries room-specific material, such as a specimen line
quoted from a room, would put private words in a public prompt. The
list is reviewed file by file in phase 2 and pinned by a test.
