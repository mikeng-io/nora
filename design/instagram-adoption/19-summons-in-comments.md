---
type: design
title: "Summons in comments: tag @noraexample under an Instagram post, and she answers in the comments"
description: "The Threads summons (parked) carried over to Instagram, on her main account, over the bridge's unofficial session. Mentions come from polling her activity feed, the summoner gate is identity.toml by igid, the occasion keeps no room and no memory, and a comment is written only after a read-only probe and under a hard cap. Phase 1 is the probe."
tags: [nora, instagram, summons, comments, public-surface, bridge, grok]
timestamp: '2026-09-25'
status: proposed
edges:
  - {dst: ../threads-adoption/10-architecture.md, rel: reuses, provenance: asserted}
  - {dst: ../threads-adoption/00-intent.md, rel: realizes, provenance: asserted}
  - {dst: 16-bridge-architecture-and-observability.md, rel: constrained-by, provenance: asserted}
  - {dst: 15-bridge-is-the-only-entry.md, rel: constrained-by, provenance: asserted}
  - {dst: 00-overview.md, rel: constrained-by, provenance: asserted}
---

# Summons in comments

## The decision (operator, 2026-09-25)

Threads was parked (`../threads-adoption/`): reading the post a summons
sits under needs Meta's advanced access, which costs App Review plus
business verification. The operator moved the concept to Instagram and
chose, of three options, **her main account, carefully**. The other two were
a separate public account, and keeping the fact check inside group chats.

The official Instagram Platform API hits the same wall as Threads: mention
and comment webhooks for non-testers need advanced access, a
professional account, and business verification. So this runs on the
instagram-bridge's own session, the unofficial web API, which design 15
already made the only door to Instagram.

## What carries over unchanged from the Threads design

`../threads-adoption/10-architecture.md` is the reference for all of these.
None of them is Threads-specific:

- **A summons is its own occasion.** It shares the model loop, the tool
  surface and tracing, but constructs no room and writes no memory
  (§"The occasion").
- **Public tools only**, through `ToolScope.grant` (§"`PUBLIC_GRANT`"),
  **minus `generate_image`**: a comment carries no image (see the table
  below), so the Instagram grant is the Threads one without it.
- **A staging deliverer** that reuses `send_reply` and publishes once. Nora chooses what to say, never where: the reply target
  is the summons comment, fixed before the model runs.
- **One reply per summons, idempotent**, claimed in Postgres before the
  model runs.
- **The summons context is comprehended once and rendered with ages**, as
  untrusted content.
- **A public-surface doctrine**, loaded only for this occasion.

## What Instagram changes

| | Threads (parked) | Instagram |
|---|---|---|
| door | official Graph API, `threads-bridge` sidecar | the existing instagram-bridge, one session shared with her group chats |
| ingress | `GET /{uid}/mentions` | her activity feed, polled through the bridge: `GET /mentions` over `/api/v1/news/inbox/` (unmeasured, phase 1) |
| summoner identity | username only; the API exposes no id | **the actor's igid**, expected in the activity story's `profile_id` (a hypothesis until phase 1 reads a real story). An id, not a name, so the gate needs no pinning |
| gate | `[[persons.<id>.threads]]` by username | `[[persons.<id>.instagram]]` by `igid`, the block `identity.toml` already has |
| context | the post and its reply chain | the post (`GET /posts/{shortcode}`, live) plus the comment thread (new route, unmeasured) |
| reply | a Threads reply post, 500 characters | a comment reply (`replied_to_comment_id`); length limit unmeasured |
| images | a public URL for Meta to fetch | a comment carries no image. `generate_image` is **not** granted here; an image answer is out of scope |
| the cost of getting it wrong | a rejected API call | a checkpoint or ban on the account her group chats run on |

The last row is the whole reason for the "carefully" below.

## Account safety: what "carefully" means

design/instagram-adoption/00-overview says checkpoints are routine on an
unofficial client and that a ban can come without warning and without
appeal. The same session carries her group chats (design 08 §"The one place
the two axes touch"), so a public-comment bot that gets flagged takes her
out of Instagram entirely. The rules:

1. **Read before write.** Phase 1 only reads the activity feed. No comment
   is written until phase 1 has shown what the feed returns and the
   session has survived a week of polling it.
2. **Every call goes through the bridge's session with the web client's own
   headers.** A request built by hand with copied cookies looks like a
   second client, and that is how a session gets flagged. That is design
   16's one-way-to-call rule, and it matters more here than anywhere else.
3. **A poll interval measured in minutes, not seconds** (default 120 s,
   chosen, not measured: how often the web client polls its own
   notifications is unknown). A summons is answered minutes late, which a
   researched public answer can afford.
4. **Writes behind a hard cap in the bridge**, not in Nora: at most N comments
   per rolling 24 hours (start at 10), enforced in the process that writes.
   A checkpoint seen by ANY bridge call, `GET /mentions` included, goes
   through the same path the socket's auth errors take: the session state
   becomes `checkpointed`, the session disconnects, and every read and write
   stops until a human clears it. The first version of the route did not do
   this; review of #529 caught it.
5. **Only `identity.toml` people can summon her** (operator rule, carried
   over). A mention from anyone else is counted and ignored. This is also
   the volume control: the number of summoners is the number of friends.
6. **No reply to a post she cannot read in full.** If the post read or the
   comment read fails, she stays silent. She never answers a claim she has
   not seen.

## Phases

Each phase ends in a live read-back and is one PR.

**Phase 1: read the activity feed (read-only, this PR).** A bridge route
`GET /mentions` over `/api/v1/news/inbox/`, which classifies comment
mentions by structure (a comment id, media, and a mention of her) and counts
every other story by `story_type`. It is deployed, then:

- **before the first live call**, the exact request the Instagram web client
  makes for its notifications panel is captured from a real browser (method,
  path, query, headers). The route's endpoint is a hypothesis
  (`/api/v1/news/inbox/`, which is not in the library's endpoint table), and
  a request the web client never makes is the mismatch rule 2 forbids,
- the operator comments `@noraexample is this true?` under a post: his own
  first, then a stranger's public post,
- `GET /mentions` is read back. **Pass:** the summons appears with the
  actor's igid, the media id and the comment id, under both posts.
- **Fail modes that change the design:** the endpoint needs a different
  client (the app, not the web), the mention is absent, or a stranger's
  post gives no media id. Each one ends the Instagram summons as it is
  designed here.

**Phase 2: read the comment thread.** `GET /posts/{shortcode}/comments`
around the summons comment, over `/api/v1/media/{id}/comments/`.

**Phase 3: the occasion, dry-run.** This is the Threads occasion with an
Instagram deliverer whose flush writes the would-be comment to the outcome
row and the trace. No write route exists yet.

**Phase 4: write.** The bridge gets `POST /posts/{shortcode}/comments` with
`replied_to_comment_id`, behind rules 4 and 6. It goes live only after a
week of phase 1 polling without a checkpoint.

## What would stop it

- **A checkpoint caused by the polling** in phase 1. Polling stops, and the
  operator decides between option 2 (a separate account) and stopping.
- **The feed does not carry comment mentions through the web endpoint.** The
  mobile-app endpoint would need a different client identity from the one
  the session is logged in as, which is exactly the mismatch rule 2
  forbids.
- **A stranger's post cannot be read** by `GET /posts/{shortcode}`. A
  private account Nora does not follow is the expected case. A public one
  is unmeasured.
