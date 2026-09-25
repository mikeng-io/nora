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
| ingress | `GET /{uid}/mentions` | **Superseded 2026-09-26 (see "Intake, re-measured 2026-09-26"): a DM-thread card, not the feed.** Was: her activity feed, polled through the bridge: `GET /mentions` over `POST /api/v1/news/inbox/`, form `jazoest`, `fb_dtsg`, `selected_filters=comments` (the web client's own request, captured 2026-09-25; the response to a real comment mention still unread, phase 1) |
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
3. **(Superseded 2026-09-26: there is no feed poll; the intake is the DM
   card, one thread read per card. Kept for the record.)** **A poll interval measured in minutes, not seconds** (default 120 s,
   chosen, not measured: how often the web client polls its own
   notifications is unknown). A summons is answered minutes late, which a
   researched public answer can afford.
4. **Writes behind a hard cap in the bridge**, not in Nora: at most N comments
   per rolling 24 hours (start at 10), enforced in the process that writes.
   A checkpoint seen by `GET /mentions`, and by every route this design
   adds, goes through the same path the socket's auth errors take: the
   session state becomes `checkpointed`, the session disconnects, and every
   read and write stops until a human clears it. This covers a redirect, a
   4xx body and a 200 carrying `"status":"fail"`. The first version of the
   route did not do this; review of #529 caught it. The existing raw-GraphQL
   profile read does not do it either. That gap is older than this design
   and is tracked separately.
5. **Only `identity.toml` people can summon her** (operator rule, carried
   over). A mention from anyone else is counted and ignored. This is also
   the volume control: the number of summoners is the number of friends.
6. **No reply to a post she cannot read in full.** If the post read or the
   comment read fails, she stays silent. She never answers a claim she has
   not seen.

## Phases

**Order changed (operator, 2026-09-25):** every bridge route is built
before anything is tested or deployed: the mentions read (phase 1), the
comments read (phase 2) and the comment write (phase 4's bridge half). The
write ships **off** (`INSTAGRAM_COMMENT_WRITES` unset). Turning it on is a
config change made after phase 1's live read-back and the week of polling.
Building it early does not bring that date forward. The write's guards,
all in the bridge and all before any upstream call:

| guard | refusal |
|---|---|
| `INSTAGRAM_COMMENT_WRITES=on` | 403 `writes_disabled` |
| the session is ready | the existing not-ready answer |
| ~~`reply_to_comment_id` is a comment this bridge served from `GET /mentions`, on the same media, within 7 days~~ superseded 2026-09-26: a summons card for this media within 7 days, and the post's first comments page shows `reply_to_comment_id` by that card's sender, mentioning her (see "Intake, re-measured 2026-09-26") | 403 `not_a_summons` |
| no earlier reply to that comment (claimed or published) | 409 `already_replied` |
| text non-empty, at most 2,200 characters | 400 |
| at most `INSTAGRAM_COMMENT_DAILY_CAP` (10) replies per rolling 24 hours | 429 `daily_cap` |
| at least `INSTAGRAM_COMMENT_MIN_INTERVAL` (90 s) since the last one | 429 `too_soon` |

The reply is claimed in the ledger (`comments.json` in the session
directory) before the upstream call. An ambiguous failure leaves it
claimed and is never retried: a double public comment is worse than a
missing one. That is the write-side analogue of R-86: the bridge, not the
model, decides what may be written.


Each phase ends in a live read-back and is one PR.

**Phase 1: read the activity feed (read-only, this PR).** **Superseded
2026-09-26:** the feed never carried a comment-mention; the intake is the DM
card (see "Intake, re-measured 2026-09-26"). `GET /mentions` stays as a read
and authorizes no write. The original text follows. A bridge route
`GET /mentions` over `/api/v1/news/inbox/`, which classifies comment
mentions by structure (a comment id, media, and a mention of her) and counts
every other story by `story_type`. It is deployed, then:

- **before the first live call**, the exact request the Instagram web client
  makes for its notifications panel is captured from a real browser (method,
  path, query, headers). The route's endpoint is not in the library's
  endpoint table, and a request the web client never makes is the mismatch
  rule 2 forbids. **Done 2026-09-25** (the operator's own browser, read
  only). What it showed:
  - the panel is `POST /api/v1/news/inbox/`, not GET, form-encoded with
    `jazoest` and `fb_dtsg`, plus `selected_filters=comments` on the
    Comments tab. The route now sends exactly that, with the form tokens
    and `X-Instagram-AJAX` from the session's own configs, and does NOT send
    the `news/inbox_seen/` POST the panel also makes;
  - no story carries `comment_id`/`comment_ids`. A comment's id rides in the
    story's `destination`, e.g. `clips_home?id=<media>&target_comment_id=<comment>`
    on a "liked your comment" story (type 13), and the route reads it there;
  - the comment read is exactly the hypothesis:
    `GET /api/v1/media/<pk>/comments/?can_support_threading=true&permalink_enabled=false`.
    The replies (`child_comments`) request was not observed: the sampled
    post had none;
  - no comment-mention story was in the feed, so its numeric `story_type` is
    still unmeasured (types seen: 651, 13, 101, 75, 768, 117). The skipped
    counts are how the first live read will show it;
  - one header stays different: the web client sent a non-zero
    `X-IG-WWW-Claim`, and the library keeps that claim in memory only, so a
    bridge that has not been handed one since its last restart sends `0`,
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

## Intake, re-measured 2026-09-26

This section supersedes the notification-feed poller (the ingress row of
the table above, rule 3, phase 1 and the guard-3 row of the write's table).
That text is kept, marked, for the record.

### What was measured

- A comment (or a caption) mentioning `@noraexample` reaches her as a
  **DM-thread message**, a `NewMessageEvent` on the realtime socket:
  `content_type` `MESSAGE_INLINE_SHARE`, XMA typename
  `SlideMessageLayeredXMA`, `subtitle_text` = the comment's text (for
  example `@noraexample is this true`), `xmaTitle`/`title_text` = the
  COMMENTER's username (not the post owner's), the preview image = the post
  image. The sender is the commenter (igid, fbid, username). **The live
  delta carries no `target_url`, no `target_id` and no CTA:** nothing in it
  names the post.
- The thread query (`GetThread`, `IGDThreadDetailQuery`, the read the roster
  route makes) returns the same message **with** `content.xma.target_url` =
  `https://www.instagram.com/p/<shortcode>/` (a reel: `/reel/<code>/`).
- The notification feed (`POST /api/v1/news/inbox/`) never carried these
  mentions.
- The comment's id is **not** in the card. It is found by reading the post's
  comments (`GET /api/v1/media/<pk>/comments/?can_support_threading=true&permalink_enabled=false`,
  confirmed) and matching author igid = the card's sender igid, text
  carrying the word-bounded `@` handle.

### The intake (bridge)

For a `NewMessageEvent` with `content_type` `MESSAGE_INLINE_SHARE` whose XMA
typename is `SlideMessageLayeredXMA`, sent by somebody other than her, whose
**subtitle** carries word-bounded `@<her username>` (ASCII case folding; a
sentence-final `.` is punctuation), the bridge (`gateway/instameow/summons.go`):

1. emits the ordinary `message` event as always (the adapter keeps dropping
   unknown-policy threads);
2. refetches the thread **once** through `GetThread` on the short id, off
   the socket goroutine, 30 s timeout, deduplicated by message id (a bounded
   set of the last 1,024), within a budget of 6 refetches per 10 minutes
   across all senders (past it the card is emitted unresolved and costs no
   read), a checkpoint on it flipping the session like any read;
3. finds the message on the returned first page, reads `target_url`,
   parses the shortcode out of `/p/<code>/` or `/reel/<code>/` and decodes
   the media pk;
4. when that worked, records a **served summons** in `comments.json`
   (message id -> media pk, sender igid, received at; pruned at 7 days;
   atomic and fsynced like every ledger write) BEFORE the event goes out;
5. emits a new SSE event:

```json
{"kind":"summons","message_id":"mid.$...","room_id":"<short thread id>",
 "sender":{"fbid":"...","igid":"...","username":"..."},
 "text":"<subtitle_text>","shortcode":"<code>","media_id":"<pk>",
 "received_at_ms":1790000000000,"resolved":true}
```

`resolved` is false, with `shortcode` and `media_id` empty, when the
refetch failed or was over budget, the message was not on the first page,
it carried no usable `target_url`, or recording the served summons failed. Nora records that and does not answer.
`received_at_ms` is the card's own timestamp (the bridge's clock when it
carried none). Log lines carry fixed messages, a boolean and a reason word:
never an id, a username or text.

### Write guard 3, replaced

`POST /posts/{shortcode}/comments {text, reply_to_comment_id}` now requires,
before any upstream write:

- **(a)** a served summons for this media pk within 7 days, and
- **(b)** the bridge's own read of the post's first comments page shows
  `reply_to_comment_id`, written by the sender of such a summons and
  carrying word-bounded `@<her username>`.

Anything else is 403 `not_a_summons`, fail closed: a comment beyond the first
page, a failed read and an unknown own username included. (b) is the one
guard that calls Instagram, and it is a read: it runs only after the latch,
(a) and guards 4 to 7 have passed, so no other refusal costs a call. The
claim transaction then re-runs every ledger guard and re-checks the author
against the live summonses, so a window that closes during the read
authorizes nothing. All other guards and their order are unchanged.
`GET /mentions` stays as a read and no longer authorizes a write; a
`comments.json` that still holds its old `served` key loads, and the key is
dropped on the next write.

### The intake (Nora)

- **Intake.** `nora/platform/instagram/summons.py` parses the event once into
  a `SummonsCard` (fail closed: no usable message id, time or boolean
  `resolved` is refused and counted `malformed`). The adapter hands it to
  `nora/app/summons_inbox.py` (bounded, drops the oldest, counted on
  `nora_summons_intake_total`) and never to room ingest; with
  `[social.instagram.summons]` off every card is counted `disabled` and
  dropped. `nora/app/summons_consumer.py` works one card at a time, retries a
  live `staged` flush first, backs off on `needs_human`/503, and runs the
  stale-claim sweep at boot and every step. The poller and Nora's
  `GET /mentions` read are gone; `poll_seconds` is accepted and read by
  nothing.
- **Occasion.** Claimed on the card's `message_id`. `resolved = false` is
  `unresolved` (no read, no model call). The summoner gate is unchanged
  (sender igid). Stale is `received_at` against `max_age_minutes`. The
  comment is FOUND: author igid == sender igid AND the word-bounded
  `@handle`; of several, the one whose text equals the card's (whitespace
  normalised), else the newest; none is `unreadable`. Its id is bound to the
  row once (`comment_id` stays unique, so two cards for one comment get one
  answer, the second `duplicate`) and is the `reply_to_comment_id` of the
  one write. Everything from the turn to the flush is unchanged.
- **Ledger.** `0067_summons_message_key`: `message_id` NOT NULL UNIQUE
  (pre-existing rows backfilled `legacy:<comment_id>`), `comment_id`
  nullable and still unique, state `unresolved` added.

### Reading the post's media before the turn (2026-09-26)

Measured on a live dry-run trace: the summons post was a reel, she called
`media_fetch` once with the signed CDN url as a `code` instead of a `url`,
it failed, and she answered from the caption. A summons is a tag, and
shared content is read when she is tagged (#435), so the post's items are
now read BEFORE the turn and are part of what she reads:

- **The `instagram_read` mechanism, not a second one.** `nora/app/summons.py`
  `_read_items` runs `readings.read_view` over the items she is shown, each
  look `media_fetch(url=...)` from the turn's own tool set, through
  `tools.media_look` (the one label-and-persist wrapper `instagram_read`'s
  items use too). The caption is not in the view, so it reaches no vision
  prompt; the per-video length and count caps are 18's.
- **Bounded.** At most `MAX_ITEMS_SHOWN` = 4 items (one wave of four looks;
  the rest are counted, never read), one budget of `MEDIA_READ_SECONDS` =
  150 s for all of them (one look's ceiling: the 82 s reel measured in 18,
  with headroom). An item that fails, times out or never started renders
  "could not be read"; the turn runs either way. The ages she reads are
  measured from when the read ended.
- **The pre-read stores nothing.** Its url reads keep no bytes and write no
  `media_objects` row; with `persist` off (the story frame's switch) they
  write neither content cache. Counted on
  `nora_media_fetch_total{caller="summons"}`; the stage span
  `summons.read_items` carries counts and states only. A `media_fetch` she
  makes herself is an ordinary model read and caches as any does.
- **Rendered inside the post's `social_post` block**, one reading line
  under each item: facts only ("could not be read", "not read: longer than
  one reading window"), with no instruction to her and no pointer to a
  tool; what she does with an unread item is `public/conduct.md`'s call.
  `media_fetch` stays granted for anything else.
- **Each claim its own check.** A summons turn may call `web_search` 8 times
  (`SUMMONS_WEB_SEARCHES`), not the registry's per-turn 4: `public/conduct.md`
  v3 asks for one check per claim she judges, and a live post named seven
  companies. A room tag keeps the registry's value; `x_search` is unchanged.

## Two things the review of #545 recorded

- **The reply tool's own text still speaks of "the room".** `send_reply`'s
  description (shared with every occasion) mentions the room, the
  transcript and `reply_to`. It is left as is. `public/constraints.md`
  already says that "the room" on this surface means the thread, and a
  summons-specific tool description would be a second copy of `send_reply`
  to drift. Reopen this if a summons trace shows her reasoning about a
  room.
- **The rendered prompt reaches Langfuse**, as on every turn: the
  strangers' usernames, the caption and the comments are in the
  generation spans. The occasion's own span attributes, metric labels and
  log lines carry only the ledger id. So "telemetry carries no identifier"
  holds for Nora's instruments, and not for the model trace, which exists
  to record what the model read.

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
