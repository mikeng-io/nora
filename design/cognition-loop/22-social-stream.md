---
type: design
title: "The social stream — one source interface, an adaptive per-person poll, and a back-fill of past posts and reels"
description: "How step 5 ingests members' social items: a network-neutral source interface (Instagram first, Threads later), a two-stage poll whose probe (the profile's newest-story time and post and reel counts) decides whether a costly read runs, a per-person cadence learned from when that person posts and raised by what is happening now, one budget and one halt per login, the custody rule (everything kept, except a story frame's media bytes, which expire once embedded), the social_item and social_poll Record tables, and the back-fill of past posts and reels over the web app's timeline query (found in S0)."
tags: [nora, observe, social, instagram, threads, stories, posts, reels, polling, backfill, custody]
timestamp: '2026-10-17'
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
  list_items(account, after) -> Page[SocialItem] # posts and reels, newest first
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
that person's items being read at all»). The switch is `social = false` on the
person's `identity.toml` block (default on). As built in S2 it is enforced on
membership: `SocialSubjectIndex.members` leaves them out, so nothing of theirs
is probed or read. Since S3 the read side holds it too: every read of
`social_item` for perception or for a stimulus
(`SqlSocialStreamStore`, given the opted-out persons at construction) leaves
out their items, including any recorded before the switch was set, and every
item with a `social_item_state` row (gone or hidden). The poller's own dedupe
and marker reads are the writer's state and are not filtered.

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
  at `probe_cost`, reads at 1 (starting at 60), with jitter before every call,
  a tick's first included. The
  ceiling alone costs `members × probe_cost / 6` an hour: 10 members is 3.3
  of 60, and the ceiling stays inside the budget up to 180 members. A unit
  with more members gets fresher reads for the active ones, not more calls.
- **Halt and back-off**, keyed on what the bridge actually answers
  (`deploy/instagram-bridge/internal/transport/http/errors.go`):
  - `409 needs_human` (checkpoint or logout) **halts** every social poll for
    the login. It resumes only when the bridge's `/health` reports the
    session healthy; it is never retried on the tick ("retrying this is how a
    captcha becomes a ban", errors.go). A cleared halt resumes on the
    back-off ladder's top step (6 hours) before the next probe. **Two halts
    within 24 hours lock the login** until an operator clears it, by
    appending a `source_health` row (`source = 'social.instagram'`,
    `scope = ''`, `event = 'transition'`, `outcome = 'cleared'`, `at_ms` now);
    a restart does not clear it.
  - `502 upstream_rejected` on a probe or read, or a `social_error` starting
    with `429`, **backs off** the login: polling pauses, starting at 15
    minutes and doubling to the ceiling's 6 hours, reset by one clean probe.
  - `503` is the bridge healing itself: the call is skipped, not counted as
    a back-off, and that member is tried again after the floor. A probe whose
    answer will not change on its own (not found, unsupported, an id that is
    not an id) waits for the ceiling. The order is least recently probed
    first, answered or not, so a member whose probes keep failing never
    holds the front of the queue.
  - A GraphQL error naming one of §1a's documents is `stale_query`: a
    rotated `doc_id`. It halts **that read** for every member until the id
    is captured again, is recorded as a `source_health` transition, and does
    not count toward the login's back-off, since the login is fine.
  Every halt and back-off is a transition in `source_health`
  ([20](20-observe.md) §1), so silence during it is never read as absence.
  The halt, the lock and the back-off (its step and its end) are rebuilt
  from those transitions when the poller starts, and the hour's spent
  calls from `social_poll`, so a restart neither probes early nor resets
  the doubling.
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
| `story_frame` | kept | hard-deleted at `expires_at` plus `[social] story_media_keep_days` (starting at 7) once perceived and embedded, or plus `story_media_keep_max_days` (starting at 30) regardless, leaving the tombstone of [70](70-migration.md) §9 (id, kind, time) |

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

As built in S3b (`nora/app/social_expiry.py`, migration `0109_blob_tombstone`):
**off unless `[social] story_expiry_enabled`** (default false; the pass is
not started while it is off, so setting it back to false and recreating is
the kill switch). On, it is a pass on the poller's tick period, wired wherever
media custody is and started whether or not the poller runs, so bytes
already kept still go once the poller is switched off. It reads every story
frame (any person, any state) whose bound has passed and whose bytes were not
deleted since it was recorded, decides with
`nora.domain.social_stream.bytes_due`, and deletes through the `BlobStore`
port's `delete`. The pass does not read the index yet: it still passes
"embedded" as false, so every frame's bytes go at the hard limit; "perceived"
is a `social_perception` attempt that ended `ok`. The index projects social
items since 2026-10-16 (§9), and the read it is to make is built
(`nora.infra.sql.memory_index.story_frame_embedded`); wiring it into the pass
is this stream's follow-up. **Shared bytes stay**: one
content address can be named by a room message, a post, a sticker, the
computer audit, a document or a `media_objects` row another source wrote
first, and the pass deletes only what nothing names but the frames it is
letting go (`nora.infra.sql.blob_refs.held_elsewhere`, which reads every
column that names a key and is held to it by `tests/infra/test_blob_refs.py`:
every key-shaped and every JSON column is read or excused with a reason),
asked again for each address just before its deletion. **A writer racing
the deletion** (a room message storing the same bytes before it writes the
row naming them) is covered in the store: `delete` keeps bytes put within
the last hour by the store's own clock, a `put` of held bytes counting as a
put, and the check and the removal are one step against `put` (on the
filesystem a rename aside, then the age read there, a file a crash left
aside being put back on the next process's first use; on RustFS a
`DeleteObject` conditional on the `ETag` read, `If-Match`, which the
deployed RustFS refuses with 412 once the object changed,
`tests/integration/test_rustfs_delete_is_conditional.py`). The tombstone
(`blob_tombstone`: content address, `kind = 'story_frame'`, `reason`
`embedded` or `hard_limit`, `deleted_at`; APPEND-ONLY, one row per deletion)
is written only once the store confirms the bytes are gone (`deleted`, or
`absent` after a crash between the two); bytes it kept, or a deletion that
failed, leave none and are tried on the next tick. **A tombstone is about the
rows recorded before its `deleted_at`**, read off the database's clock (the
one `recorded_at` is on) before the deletion: bytes stored again later (a member posts the same
picture) belong to the rows written since, so a later post is perceived
normally, a later story frame is deleted at its own bound, and an earlier
frame whose bytes went no longer holds them. An item whose bytes all went before any read is recorded by the
perception pass as `failed`, reason `expired`, with no model call. Measured
by `nora_social_story_bytes_deleted_total{reason}` and
`nora_social_story_unembedded_total`.

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
  `stage` (`probe`, `story`, `items`, `backfill`), `outcome` (`unchanged`,
  `story_due`, `items_due`, `partial`, `count_fell`, `read_ok`, `halted`,
  `backed_off`, `error`, and for a back-fill page `abandoned` when its cursor
  was judged bad and `unavailable` when the bridge answered nothing, §7), a
  back-fill page's next `cursor` (§7), the probed
  `latest_reel_media`, `media_count` and `total_clips_count` (null when
  absent), whether this row advanced the marker and whether it advanced the
  counts (a probe that made no list read due, or a completed list read; a
  probe that made one due does not, so a failed list read leaves the rise
  due), the latency and
  `recorded_at`. This is the per-call detail; `source_health` holds the
  login's transitions.

Perception rows are the ones ingest comprehension already writes, keyed by
the media item (`media_objects`). As built in S3, a separate pass
(`nora/app/social_perception.py`, on the poller's tick) reads what is recorded
and unperceived, so a vision failure or a spent cap never holds up a probe:
each attempt is an APPEND-ONLY `social_perception` row (`0108`) with the
units it was charged to and the vision calls it spent (one per image or
video), and an item is read only when every unit its person is listed in can afford
what it may cost (`[social.poll] perception_daily_cap`, model calls per unit
per day, the day `[cognition] timezone`'s: one per image; per video one per
`chunk_seconds` window of its length up to `chunk_max`, all of them when the
bridge sent no length, plus the text pass). It is charged what it spent:
nothing when no model was called, a landed video's windows off its row,
otherwise the estimate. Polled items go first; past the cap, or after a
failed read, an item waits for a later day; its third failed read, or a
failure no day can change (a format nothing reads, a describer not
configured), records it `failed`, never read again. The describer is told what it
reads (story frame, post or reel, collab, and when it was posted, as an
absolute time because the description is kept), never the caption. A frame gone (§3) and a hide are
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

As built in S3: each item carries the id of the read that recorded it
(`social_item.read_id`), and the stimulus is raised once every polled item of
that read has an attempt on record (perceived, failed or capped) and the
read's own `social_poll` row is written (the poller writes it after the
read's last item, so a pass running mid-read announces nothing of it), one
per unit the person is listed in, each unit checked on its own, `ref` `social_read:<read id>`, `item` the read's
kinds in order (`story_frame,collab:post`), `at_ms` when the newest was
posted. The stimulus table keeps one `social` row per `(unit, ref)` (`0108`),
so a restart announces nothing twice. A session's agenda shows it as
`<social at="...">Ada posted 2 story frames</social>`; what the items show is
not pushed into the agenda: that is `social_items` (§6), not built.

The memory index (design/unified-memory) projects `social_item` like any
other store, since 2026-10-16: each of this stream's writes calls the
projector in its own transaction (§9;
[unified-memory/25](../unified-memory/25-memory-graph.md) §7a).

## 6. What downstream reads it

- **Character**: social items are evidence for their person, built with the
  session that reads them (`nora.language.character.PersonEvidence`,
  `SqlSocialStreamStore.polled_count` and `person_items`). S3 first built the
  count alone, and a wake on social items then handed the session nothing it
  could cite (`character_revise` took Record ids only), so it ran empty and
  moved the watermark; the count therefore came with the rest:
  - **The citation.** `social:<network>:<item id>`, the item's natural key,
    its own id space beside a message's `n` and an episode's `response:<n>`
    (`nora.domain.culture.check_revision`). It resolves only against the items
    the session was handed, which are that person's readable ones: an item of
    another person, a gone or hidden one, or one of a person with
    `social = false` (under any id they were recorded under,
    `Persons.social_off_ids`) is never handed, so citing it is refused. The
    verb's arguments pass the Dreamer's R-18/R-28 screen like every write
    verb's, and an item id written into the body is refused like a message
    number, each numeric part of a composite story id on its own.
  - **The block.** `<social_items>`: the person's readable items, under
    their id or any retired one (`Persons.former_ids_for`), not yet
    handed to a session, polled and back-filled, at most 12 a session,
    rendered newest posted first, each with its citation, what it is
    («Instagram post, co-authored»), when it was posted (a moment and an
    age), what its media shows (each reading clipped to `[media]
    comprehension_render_chars`, all of an item's together to 480
    characters, screened for identifiers at render, R-51) and its caption,
    clipped to 300 characters, as the content, which the block's
    description frames as the person's published words, never an
    instruction. In a caption every `@handle` but the author's own (by
    `identity.toml`'s Instagram `username`) is written `@…`. The block's
    captions and readings are at most 9,360 characters, about 10,800 tokens
    of dense Cantonese a model call, about 9% of `[dreamer.budgets.culture]`'s
    `total_tokens`. Never its `location`: a person session holds no presence
    read, its account rendering in rooms whose `location` flags differ
    (R-31). Never `alt_text`, tagged or mentioned handles (§9's reason).
  - **The wake and the hand-off.** `person_wake_batch` (DEC-290,
    [20](20-observe.md) §3) counts the person's new messages plus their
    polled items not yet handed. Back-filled items never count: a back-fill
    of 50 items is not 50 items of wake pressure; a session woken for any
    other reason is handed them. Items have no Record id, so they go by a
    cursor of their own (`recorded_at` of the last item handed, a
    `consolidation_watermark` row `character-person:social`): each session is
    handed the OLDEST 12 past it, and the cursor moves past them only after a
    run that is not `unavailable`, after the key's watermark. A failed run
    hands the same items again, and a back-fill of 50 is handed over five
    sessions, every item once.
- **Rollup**: the daybook's `items_posted` ([25](25-rollup.md)) is filled
  from `social_item.taken_at`, by the local day of `taken_at`: posts, reels,
  story frames and back-filled items alike, read through
  `SqlSocialStreamStore.posted`, so a gone or hidden item and every item of a
  person with `social = false` are left out like any other read. **Built**
  (S6, §8); the posting histogram of §4 is the same input and is not.
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

**As built in S5** (`nora/app/social_poller.py`, migration
`0112_social_backfill`, `[social.poll] backfill_max_days`, `backfill_max_items`,
`backfill_pages_per_tick`):

- **Where it starts.** A member's first list read (no counts ever recorded)
  is the back-fill's first page: its items are recorded `origin = backfill`
  and its `social_poll` row has stage `backfill` and the page's next
  `cursor`. A member whose first list read was recorded before S5 has no
  such row and reads the first page again, once, for its cursor only: it
  records nothing, since an item on it not yet recorded is new since their
  last list read and is the poll's to record, as news. A member whose list was never read (no counts yet, or
  a network without `LIST_ITEMS`) is not back-filled until it is. Members
  with `social = false` are not on the roster, so they are never paged.
- **How deep.** The first page records up to `backfill_max_items` items
  (default 50) whatever their age; each later page records its items newer
  than `backfill_max_days` (default 90), until the member has
  `backfill_max_items` back-filled items, the first page's included. The walk ends at
  the item limit, at a page read at a cursor that holds any item older than
  the day limit (every later page is older still), or at the end of the grid
  (`next_cursor` null). The first page ends it only when all its items are
  older, since a pinned post there can be old. An item past either limit is
  counted, not recorded.
- **The lowest priority.** After the tick's probes and the reads they made
  due, and only if none of them halted or backed off the login, each member
  whose back-fill is not done gets at most one page, least recently paged
  first, up to `backfill_pages_per_tick` (default 2; 0 is off) pages a tick.
  A page is read only while what is left of the trailing hour's budget,
  after it, still covers one probe and one read of every member on the
  roster. At the defaults that is at most 12 calls an hour of the 60, and 10
  members' 50 items (about 4 pages each past the first) take about 20 ticks.
  The reserve has a ceiling: past 19 members at the defaults no page fits,
  and the back-fill waits until the reserve counts only the members due.
  Every page goes through the same jitter, budget, halt, back-off and
  `stale_query` handling as any read; a halt or back-off on a page ends the
  tick.
- **Where it stands, across a restart.** The latest `backfill` row of the
  member that read a page or ended the walk decides, read every tick
  (`SqlSocialStreamStore.backfills`): its `cursor` is where the next page
  starts, null is done, `abandoned` is done. Nothing is held in memory, so a
  restart reads no page twice. `social_poll` stays APPEND-ONLY.
- **A bad cursor ends that member's back-fill**: the bridge refusing the
  request (`400 invalid_id`, which on a back-fill page can only be the
  cursor, since the adapter shape-checks the igid and username; recorded
  `abandoned` beside the call's own `error` row), the same cursor refused
  upstream twice in a row (each refusal also backs the login off, as any
  does), or its pages failing three times in a row for any reason but a halt
  or a stale query. An unavailable page on a tick when no other call was
  answered is the bridge healing, not the cursor: it is recorded
  `unavailable`, not counted, and the back-fill stops for that tick, so an
  outage of hours ends nobody's back-fill. A page the adapter cannot read
  while the tick's probes are answered still counts. The counts are read off the failure rows themselves, so a
  lost `abandoned` row still stops the walk after three. It is not restarted from the top: the cursor
  is opaque, so "the oldest recorded item's position" cannot be rebuilt from
  an item, and what upstream answers for a stale cursor is unmeasured; a
  failure that recurs at one depth would recur after a restart too, spending
  rejected calls on the login. What is lost is the rest of one member's
  history window.
- Back-filled items raise no stimulus and use the back-fill perception
  allowance (§5). Measured by `nora_social_polls_total{stage="backfill"}`
  (pages, by outcome), `nora_social_backfill_items_total{outcome}`
  (`recorded`, `known`, `past_limit`) and
  `nora_social_backfill_ended_total{reason}` (`grid_end`, `max_items`,
  `max_days`, `bad_cursor`). Paging past the first page has not been read
  live.

## 8. Build order

| step | ships | measured by |
|---|---|---|
| S0 | **DONE 2026-10-16**: the REST story read is broken upstream; the web app's story and timeline queries captured (§1a); `latest_reel_media` equal to the newest `taken_at` (n=1); the seen mark is a separate mutation | §1 and §1a; session `authenticated` after |
| S1 | **DONE 2026-10-16**: the bridge's story read moved onto the GraphQL story query (this also mends the live `instagram_read` story, broken today) and a new `GET /accounts/{igid}/items?after=` over the timeline query, both with `doc_id`s from config and the `stale_query` code, and a test that no read sends the seen mutation; `SocialSource` port, Instagram adapter (`PROBE`, `STORY`, `LIST_ITEMS`, `READ_ITEM`), `social_item`, `social_poll`, `social_item_state`, each declared `APPEND_ONLY` | unit tests on the port, the marker and the dedupe; migration heads; the bridge read back live (§1) |
| S2 | **DONE 2026-10-16, off by default** (`nora/app/social_poller.py`, `[social.poll]`): probe-then-read on the ceiling cadence with the 30-minute floor, one hourly budget with jitter, halt and back-off per login with `source_health` transitions (rebuilt on restart, a cleared halt resuming on the top back-off step, two halts in 24 hours locking the login until an operator clears it), a first-ever list read's items recorded `origin = backfill` (§7, first page only), each new item's media fetched into custody, the per-person off switch (`social = false` in `identity.toml`, §2), and the story rule's test rewritten ([20](20-observe.md) §2). The bridge comments already described the poller after S1 | a week of `social_poll` rows; the login unharmed |
| S3a | **BUILT 2026-10-16, with the poller** (`nora/app/social_perception.py`, migration `0108_social_perception`, `[social.poll] perception_daily_cap`): each new item's media perceived once by ingest's comprehension job (images and video), told what it reads, under the per-unit daily cap, polled first, back-fill at most half of what polled items leave (§7), an item past the cap or after a failed read tried again on a later day (§5); one `social` stimulus per completed read and unit once its items are settled, none for back-fill, once across a restart (§5); the read-side exclusion of opted-out persons and gone or hidden items (§2) | `nora_perception_total{source="social",outcome}`; `nora_stimulus_total{kind="social"}`; `social_perception` rows per unit per day. Polled items in Character's wake count, with the `social:` citation and the `<social_items>` block, built 2026-10-17 (§6) |
| S3b | **BUILT 2026-10-16, the deletion half, off by default** (`nora/app/social_expiry.py`, `nora/infra/sql/blob_refs.py`, migration `0109_blob_tombstone`, `[social] story_expiry_enabled`, `story_media_keep_days` and `story_media_keep_max_days`): a `BlobStore` delete on both implementations that keeps bytes put within the last hour and is one step against `put`, the [70](70-migration.md) §9 tombstone as one row per confirmed deletion, and a story frame's bytes deleted at the first bound once perceived and embedded or at the hard limit regardless, never bytes another row names, posts and reels never (§5). Nothing reaches the 30-day bound until 31 days after the poller is enabled. The index projects and embeds social items since 2026-10-16 (§9) and `story_frame_embedded` is the read; until the pass calls it, every frame's bytes go at the hard limit | `nora_social_story_bytes_deleted_total{reason}`, `nora_social_story_unembedded_total`, `blob_tombstone` rows; then, with the hand-off, story frames embedded before their bytes went |
| S4 | the learned cadence and the raise | median probe-to-`taken_at` lag before and after, from `social_poll` and `social_item` |
| S5 | **BUILT 2026-10-17** (§7): the back-fill pages each member's posts and reels from their first list read, up to 90 days or 50 items, at the lowest budget priority (after probes and due reads, at most `backfill_pages_per_tick` pages a tick and one a member, never past what the hour owes every member's probe and read); progress read off `social_poll` rows (stage `backfill`, `cursor`, migration `0112_social_backfill`); a bad cursor ends that member's back-fill. Paging past the first page not yet read live | items per member against `media_count`; `nora_social_backfill_ended_total{reason}` |
| S6 | **BUILT 2026-10-17**: the daybook's `items_posted` counts a person's readable items by the local day of `taken_at` (`SqlSocialStreamStore.posted`, `nora.domain.rollup.daybooks`); 0 once the person's accounts have been observed that day, NULL before their first answered `social_poll`, for a person with no account and for one who opted out; a back-filled item reaches its past day on the next run, the rollup recomputing every day from the Record; `CODE_VERSION` bumped so the build in force is rebuilt | `tests/integration/test_rollup_social.py` |

S4 needs a week of S2's rows to learn from.

## 9. Decided: projecting `social_item` into the memory index

The five questions S3b's hand-off left for the index's owner were decided by
the operator on 2026-10-16 (memory-index owner) and built the same day; the
detail is [unified-memory/25](../unified-memory/25-memory-graph.md) §7a.

1. *The node.* Both, like a room attachment: one `social` node per item (its
   text a label for what it is, the caption and its media's readings) and one `social_media` node per `(item, sha256)`, `attached_to`
   the item, so a carousel is several. 25 §2's `attached_to` and `shared_by`
   now name a social item's medium; a medium's type for the embedder is read
   from `media_objects.media_type`, since it has no message.
2. *Its scope.* Person-scoped like a stay: `persons` is the author, no unit
   and no room, readable only where a room lists them. Not the presence
   rule. Tagged and mentioned people get no `persons` entry and no edge.
3. *The off switch.* Filtered at read: the `memory_search` tool computes the
   listed persons with `social = false` per read, and every index read
   leaves their social nodes out, an item recorded before the switch
   included. The projector stays pure.
4. *Its text.* The caption, verbatim like a message body: an @mention inside
   it is the author's own text, as in a message. Never `location`, never the
   tagged or mentioned handles, and (amended the same day, operator) never
   `alt_text`: the network's generated alt text is typically phrased with the
   place and the tagged handles, which would bring back what leaving out
   `location` keeps out; 0 of 32 live rows held one, so nothing is lost.
5. *The verb.* No new category and no tool-text change: social items are in
   `kinds=media`, and the answer's `kind` says what an item is («Instagram
   story») with its `age`.

**Known limit.** A collab post is scoped and opt-out-checked by the polled
author only: `collab` records that another account owns it, not who, so the
co-owner's listing and `social = false` are not consulted.

A gone or hidden item has no node; every write of `social_item`,
`social_item_state` and `social_perception` re-projects its item. What is
`StoryMediaExpiry` asks `nora.infra.sql.memory_index.story_frame_embedded`
which of its perceived frames are embedded under the deployed embedder, so a
frame both perceived and embedded loses its bytes at the first bound; a frame
the index has not projected waits for the hard limit.

## 10. Open

- The seen mark is a separate mutation the stream never sends (§1), but
  no author's viewer list was read to confirm Nora is absent from it. That
  read needs a member's help: one story, read by the poller, then their
  viewer list.
