---
type: design
title: "The social stream — one source interface, an adaptive per-person poll, and a back-fill of past posts and reels"
description: "How step 5 ingests members' social items: a network-neutral source interface (Instagram first, Threads later), a two-stage poll whose probe (the profile's newest-story time and post and reel counts) decides whether a costly read runs, a per-person cadence learned from when that person posts and raised by what is happening now, one budget and one halt per login, the custody rule (everything kept, except a story frame's media bytes, which expire once embedded), the social_item and social_poll Record tables, and the back-fill of past posts and reels over the web app's timeline query (found in S0)."
tags: [nora, observe, social, instagram, threads, stories, posts, reels, polling, backfill, custody]
timestamp: '2026-10-16'
edges:
  - {dst: 20-observe, rel: extends, provenance: asserted}
  - {dst: 70-migration, rel: depends_on, provenance: asserted}
  - {dst: ../instagram-adoption/06-retention-and-custody.md, rel: amends, provenance: asserted}
  - {dst: ../instagram-adoption/17-implementation-plan.md, rel: depends_on, provenance: asserted}
---

# 22 · The social stream

Operator 2026-10-16: «nora will pre-feed human social past post/reel +
periodly check on their story - adaptive should be time sensitive. abstracted
not only IG, maybe thread in future». Instagram first.

**Naming.** This is the **social stream**, one of Observe's five streams
([20](20-observe.md) §1). It observes; it does not act. "Engagement" is not
used for it: `nora.engagement` is the inbound admission pipeline (R-12), and
an act on a network (a like, a comment) is a separate decision this node does
not make. The port is `SocialSource`, the Record kind `social_item`.

This node replaces [20](20-observe.md) §1's fixed clock for the social stream
(«at least every 12 hours») and makes §2's items concrete. What §2 rules stays:
members only, nothing crawled, the per-person off switch, non-member text
never the sole citation, and the live read verbs withheld from cognition. Play
(Steam, Discord) stays in step 5 of [70](70-migration.md) and is not in this
node.

## 1. What is measured today, and what is not

| fact | source | status |
|---|---|---|
| a member's current story | bridge `GET /accounts/{igid}/story` | the library's REST `/api/v1/feed/reels_media/` was **broken** (Instagram answered `500`, 4/4 on 2026-10-16, library v0.2609.0, the latest). **Moved** onto the web app's GraphQL story query (§1a) in S1: live, a story in 0.3 s, a member with none `found: false` |
| when a member's newest story was posted, without opening it | bridge `GET /accounts/{igid}`, top-level `latest_reel_media` (`reads.go:140`; `has_story` is `!= 0`) | built |
| how many posts and reels a member has | the same route's `social` half: `social.media_count`, `social.total_clips_count` (`reads.go:207-208`) | built; a **second upstream call** that fails on its own (`social_available: false`, `social_error: "<status>: …"`, `reads.go:240-254`) |
| one post or reel by shortcode | bridge `GET /posts/{shortcode}` | built |
| **a list of a member's posts or reels** | bridge `GET /accounts/{igid}/items?after=&username=`, over the web app's GraphQL timeline query (§1a) | **built** (S1, 2026-10-16): live, 12 items a page, newest first, with a cursor; paging past the first page not yet read live |
| what a timeline page carries besides its items | S1 live read-back | **every page** came back with a `field_exception` error beside its edges, in the logged-in browser too; the bridge reads the page and refuses only a page with no edges |
| whose items a member's grid holds | S1 live read-back | **collaboration posts**: 11 of one member's first 12 were owned by another account with the member as co-author. The bridge lists them with `collab: true`, and `social_item.collab` records it; an item whose owner fields disagree, or that names the member nowhere, is refused |
| whether `latest_reel_media` equals the newest frame's `taken_at` | S0 | **equal** on the one story read (n=1): the probe value and the newest of 3 frames were the same second. §3's marker does not rely on it |
| a frame's expiry | S0 | **per frame** on the one reel read (n=1, 3 frames): each item's `expiring_at` was its `taken_at` + 86400, and the reel's own `expiring_at` was null. The stream stores each frame's own expiry |
| whether reading a story marks it seen to its author | S0 | **a separate call**: the web viewer marks a story seen with `PolarisStoriesV3SeenMutation`, sent after the read. With it blocked, the reel's `seen` stayed `0`. The stream sends the read only, never the mutation |

What the official API can do here, from Meta's documentation and not measured
by this repo: the Instagram Graph API lists only the authenticated
professional account's own media; Business Discovery reads another *business
or creator* account's public media, never a personal account and never
stories; the Basic Display API was shut down on 2024-12-04. Members are
personal accounts, so the official API does not serve this stream.

### 1a. The web app's queries (S0, 2026-10-16)

Captured from the logged-in web client on norty (OpenCLI's `nora` Browser
Bridge profile, the login the bridge's session is synced from), one member,
with the seen mutation blocked in the page:

| read | friendly name | `doc_id` | variables | response root |
|---|---|---|---|---|
| a member's story | `PolarisStoriesV3ReelPageStandaloneQuery` | captured | `reel_ids_arr: [igid]` | `xdt_api__v1__feed__reels_media.reels_media[]`: `items[]` with `pk`, `taken_at`, `expiring_at`, `media_type`, `video_versions`, `image_versions2`; reel `latest_reel_media`, `seen` |
| a member's posts and reels, newest first | `PolarisProfilePostsTabContentQuery_connection` (`/graphql/query`) | captured | `username`, `first: 12`, `after` (cursor), `data.count: 12` and the relay provider flags | `xdt_api__v1__feed__user_timeline_graphql_connection.edges[].node`: `code`, `pk`, `taken_at`, `caption`, `video_versions`, … and `page_info` |

Both are the shape `social_profile.go` already sends for
`PolarisProfilePageContentQuery` through the bridge's session. That the
bridge's session is answered for these two documents is **assumed**: they
were captured from OpenCLI's browser, logged into the same account, and S1's
first read-back is the test. The two `doc_id`s are not
written here (they are id-shaped, and the repo's PII hook refuses them):
they go into the live config at S1, and §1a's capture recovers them. **A `doc_id`
rotates** when Instagram ships a new web build: each is config, never a
constant. What a rotated id answers is not measured; S1 maps any GraphQL
error naming the document to the bridge's own code (`stale_query`), which
halts that read (not the login) until the id is captured again the same way.

## 2. One source interface, per network

Each network implements one port; the poller and the Record never name a
network.

```
SocialSource
  network: str                       # "instagram"; "threads" later
  capabilities: frozenset[Cap]       # PROBE, STORY, LIST_ITEMS, READ_ITEM
  probe(account) -> Probe            # newest-story marker; item counts, or why absent
  story(account) -> StorySnapshot    # found, frames with taken_at, expires_at
  list_items(account, before) -> Page[ItemRef]   # posts and reels, newest first
  read_item(ref) -> SocialItem
  probe_cost: int                    # upstream calls one probe spends (Instagram: 2)
```

A capability a network lacks is absent, not an error: Threads has no stories,
so it never exposes `STORY`, and the poller never schedules a story read for
it. Instagram exposes all four from S1: `LIST_ITEMS` over the timeline query
S0 found (§1a). The port sits beside `SocialRouter`
and goes through the same doors: for Instagram, the bridge only
(instagram-adoption/15).

Accounts come from `identity.toml` through the one membership function of
[20](20-observe.md) §2. A member with no account on a network is not polled
there. A member whose off switch is set is not polled anywhere, and their
recorded social items are excluded from every read, as §2 requires («stops
that person's items being read at all»).

## 3. The poll: a probe decides, then the costly read

Every poll of a member is two stages:

1. **Probe** (one bridge call, `probe_cost` upstream calls). The probe's
   `latest_reel_media` is compared with the person's **read marker**: the
   probe value that led to their last *completed* story read, stored on
   `social_poll`. A different value makes a story read due. The marker is the
   probe's own value, never a frame's `taken_at`, so the detector does not
   depend on the two being the same clock (§1: equal, but n=1). The counts are
   compared with the last recorded counts; a rise makes a list read due. A probe whose `social` half failed
   (`social_available: false`) is outcome `partial`: the story comparison
   stands, the count comparison is skipped, and an absent count is never read
   as "unchanged".
2. **Read** only what the probe said changed, **at most once per new marker
   value**. A completed read, including one that returns `found: false` or no
   frames (a close-friends story Nora cannot see, a story already deleted),
   advances the marker, so the same value never triggers a second read. Each
   frame or item is keyed by `(network, item_id)`; one already recorded is not
   written again, and only a new item goes to perception.

The probe keeps the common poll to one bridge call and no vision call, and
the story read rare, which is what the login risk ([20](20-observe.md) §2)
asks for. A count that fell is recorded on the poll row (`count_fell`) and
nothing else changes.

**A frame the author deleted.** A story read that no longer shows a frame
recorded earlier, before its `expires_at`, records that the frame is gone and
hides it from every read (§5), the way a platform delete hides a message.
Detection is best-effort: deletion only shows on a read, and reads happen on
marker changes.

## 4. The cadence: per person, learned, and raised by what is happening

A story lives 24 hours, so any interval under 24 hours sees every frame. The
cadence is about **freshness**, how soon after posting Nora knows, and about
spending a fixed budget where it buys the most.

- **Learned rhythm.** Each member has a posting histogram over local hour of
  week, built from the `taken_at` of their recorded items (a Rollup aggregate,
  [25](25-rollup.md), rebuildable). The expected number of new items since the
  last probe, `λ(person, now)`, is read from it.
- **Raised by what is happening now.** `λ` is multiplied (starting at 3) when
  the Rollup has an open outing or co-location episode for the person, the
  Register has an event of theirs dated today, or the Record has a message
  from them in any of the unit's rooms in the last hour. People post during
  the things they do; a story about tonight's dinner matters tonight.
- **Order.** Each tick (starting at every 10 minutes) the poller first probes
  every member overdue for the ceiling, oldest first, then the highest
  `max(λ, ε) × hours since last probe`, while the budget allows. `ε` keeps a
  quiet or cold member from scoring zero.
- **Bounds.** No member is probed more often than every 30 minutes; every
  member is probed at least every 6 hours. The cold start (no history) is the
  ceiling.
- **Budget.** One per-login hourly cap on **upstream calls**, probes counted
  at `probe_cost`, reads at 1 (starting at 60), with jitter on every call. The
  ceiling alone costs `members × probe_cost / 6` an hour: 10 members is 3.3
  of 60, and the ceiling stays inside the budget up to 180 members. A unit
  with more members gets fresher reads for the active ones, not more calls.
- **Halt and back-off**, keyed on what the bridge actually answers
  (`deploy/instagram-bridge/internal/transport/http/errors.go`):
  - `409 needs_human` (checkpoint or logout) **halts** every social poll for
    the login. It resumes only when the bridge's `/health` reports the
    session healthy; it is never retried on the tick ("retrying this is how a
    captcha becomes a ban", errors.go).
  - `502 upstream_rejected` on a probe or read, or a `social_error` starting
    with `429`, **backs off** the login: polling pauses, starting at 15
    minutes and doubling to the ceiling's 6 hours, reset by one clean probe.
  - `503` is the bridge healing itself: the call is skipped, not counted as
    a back-off.
  - A GraphQL error naming one of §1a's documents is `stale_query`: a
    rotated `doc_id`. It halts **that read** for every member until the id
    is captured again, is recorded as a `source_health` transition, and does
    not count toward the login's back-off, since the login is fine.
  Every halt and back-off is a transition in `source_health`
  ([20](20-observe.md) §1), so silence during it is never read as absence.
- **What is guaranteed.** Every frame is seen before it expires **while the
  login is healthy**: the 6-hour ceiling leaves 18 hours. A halt longer than
  that loses frames, and the `source_health` interval says which hours were
  blind.

All starting values are config keys under `[social.poll]`, sized after a week
of `social_poll` rows, never on one sample.

## 5. Custody and the Record

**Custody (operator rulings 2026-10-16, amending
instagram-adoption/06's "social reads never enter custody").** Every item's
media is fetched into media custody within the same poll (the CDN URLs are
signed and expire, so a later fetch may find nothing) and perceived. Then the
two kinds part:

| kind | rows, caption, perception, embedding | media bytes |
|---|---|---|
| `post`, `reel` | kept, **no retention** | kept, **no retention**, like room media |
| `story_frame` | kept | hard-deleted at `expires_at` plus `[social] story_media_keep_days` (starting at 7), leaving the tombstone of [70](70-migration.md) §9 (id, kind, time) |

A post or reel is something its author published to stay; a story is
something they published to vanish, so only its bytes go.

**Embedded too (operator 2026-10-16).** Each item's image or video is
embedded by the memory index's multimodal embedder (design/unified-memory,
node 20's chosen model reads image and video) through the index's own worker,
like any other media the index projects; this node does not embed. The vector
is derived, like the perception text, and is kept when a story's bytes go. So
a story frame's byte deletion waits on it: its bytes are deleted at the bound
**only once its perception and embedding both exist**, or at a hard limit
(`[social] story_media_keep_max_days`, starting at 30) after which the frame
stays unembedded, which is counted, not an error. Every rendering
of a social item says what it is and how old it is
(instagram-adoption/06, «a story must be SAID to be a story»).

Two tables, both `APPEND_ONLY` (`nora/infra/sql/history.py`): never updated
after insert. Everything that changes about an item is a new row elsewhere.

- **`social_item`**: `(network, item_id)` unique; `person`, `account`,
  `kind` (`story_frame`, `post`, `reel`, later `thread_post`), `taken_at`,
  `expires_at` (story frames), `caption`, `alt_text`, `location`, `tagged`
  and `mentioned` handles (recorded, never read, [20](20-observe.md) §2),
  `media_ref`, `seen_at`, `origin` (`poll` or `backfill`), `collab` (another
  account's post the person co-authored, §1), `recorded_at`.
  No unit column: a person in two units is one row, and which units may read
  it is derived from membership at read time.
- **`social_poll`**: one row per probe and per read: `network`, `person`,
  `stage`, `outcome` (`unchanged`, `story_due`, `items_due`, `partial`,
  `count_fell`, `read_ok`, `halted`, `backed_off`, `error`), the probed
  `latest_reel_media`, `media_count` and `total_clips_count` (null when
  absent), whether this read advanced the marker, the latency and
  `recorded_at`. This is the per-call detail; `source_health` holds the
  login's transitions.

Perception rows are the ones ingest comprehension already writes, keyed by
the media item (`media_objects`). A frame gone (§3) and a hide are
rows in an append-only `social_item_state` (`item`, `state`: `gone`,
`hidden`; `reason`; `recorded_at`), which every read joins. No row of this
stream has a retention key: posts and reels have none at all, and a story's
only bound is on its bytes (above).

**Stimulus.** A completed read that recorded new items raises **one** `social`
stimulus for that person, after its items are perceived or recorded
unperceived past the per-unit cap, so Cognition wakes on a described story,
not a URL ([20](20-observe.md) §2). Its cues are the deterministic ones of
[20](20-observe.md) §3 run over caption and perception text (a date or time
expression, a place name or tag). A back-filled item raises none (§7):
history is evidence, not news.

The memory index (design/unified-memory) projects `social_item` like any
other store. This node writes the table and nothing in the index; the index
reads it.

## 6. What downstream reads it

- **Character**: social items are evidence for their person. Today
  `person_wake_batch` counts messages only (DEC-290, [20](20-observe.md) §3);
  S3 adds polled social items to the count. Back-filled items do not count
  toward a wake: Character reads them on its next wake for other reasons, so
  a back-fill of 50 items is not 50 items of wake pressure.
- **Rollup**: the daybook's `items_posted` ([25](25-rollup.md), NULL until
  step 5) is filled from `social_item.taken_at`; the posting histogram of §4
  is the same input.
- **Cognition**: `social_items(person, interval)` ([15](15-map.md) §4).

## 7. The back-fill of past posts and reels

When a member is first polled on a network, or when a network first
exposes `LIST_ITEMS`, the poller pages their posts and reels newest first, up to
a depth limit (starting at 90 days or 50 items, whichever comes first), at the
budget's lowest priority, so it never delays a probe or a story read. Items
are written with `origin = backfill` under the custody rule of §5. Back-fill
perception may use at most half of the per-unit daily cap that remains when it
runs; past that, items are recorded unperceived and perceived on a later day.

**The mechanism is the timeline query of §1a** (S0 found it, closing P13's
investigation): 12 items a page, newest first, paged by `after`. A member
whose `media_count` read 281 in S0 is 24 pages at that size; the depth limit stops far earlier.

## 8. Build order

| step | ships | measured by |
|---|---|---|
| S0 | **DONE 2026-10-16**: the REST story read is broken upstream; the web app's story and timeline queries captured (§1a); `latest_reel_media` equal to the newest `taken_at` (n=1); the seen mark is a separate mutation | §1 and §1a; session `authenticated` after |
| S1 | **DONE 2026-10-16**: the bridge's story read moved onto the GraphQL story query (this also mends the live `instagram_read` story, broken today) and a new `GET /accounts/{igid}/items?after=` over the timeline query, both with `doc_id`s from config and the `stale_query` code, and a test that no read sends the seen mutation; `SocialSource` port, Instagram adapter (`PROBE`, `STORY`, `LIST_ITEMS`, `READ_ITEM`), `social_item`, `social_poll`, `social_item_state`, each declared `APPEND_ONLY` | unit tests on the port, the marker and the dedupe; migration heads; the bridge read back live (§1) |
| S2 | the poller: probe-then-read, ceiling cadence, budget, halt and back-off with `source_health` transitions; media fetched into custody and expired; the story rule's test rewritten ([20](20-observe.md) §2) and the bridge comments that still say «only when asked» updated (`internal/transport/http/server.go`, `internal/app/app.go`, `internal/gateway/instameow/social.go`) | a week of `social_poll` rows; the login unharmed |
| S3 | perception of new frames under the per-unit cap; the `social` stimulus; Character's wake count; `social_item` media handed to the memory index's embed worker, and a story frame's byte deletion gated on perception and embedding | `nora_perception_total{source="social"}`; stimuli per read; story frames embedded before their bytes went |
| S4 | the learned cadence and the raise | median probe-to-`taken_at` lag before and after, from `social_poll` and `social_item` |
| S5 | the back-fill over `LIST_ITEMS` | items per member against `media_count` |

S4 needs a week of S2's rows to learn from.

## 9. Open

- The seen mark is a separate mutation the stream never sends (§1), but
  no author's viewer list was read to confirm Nora is absent from it. That
  read needs a member's help: one story, read by the poller, then their
  viewer list.
