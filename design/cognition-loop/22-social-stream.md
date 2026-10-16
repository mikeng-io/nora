---
type: design
title: "The social stream — one source interface, an adaptive per-person poll, and a back-fill of past posts and reels"
description: "How step 5 ingests members' social items: a network-neutral source interface (Instagram first, Threads later), a two-stage poll whose cheap probe (the profile's newest-story time and post counts) decides whether the costly read runs, a per-person cadence learned from when that person posts and raised by what the Rollup sees, one budget that protects the messaging login, the social_item and social_poll Record tables, and the back-fill of past posts and reels, which waits on a listing mechanism (P13)."
tags: [nora, observe, social, instagram, threads, stories, posts, reels, polling, backfill]
timestamp: '2026-10-16'
edges:
  - {dst: 20-observe, rel: extends, provenance: asserted}
  - {dst: 70-migration, rel: depends_on, provenance: asserted}
  - {dst: ../instagram-adoption/17-implementation-plan.md, rel: depends_on, provenance: asserted}
---

# 22 · The social stream

Operator 2026-10-16: «nora will pre-feed human social past post/reel +
periodly check on their story - adaptive should be time sensitive. abstracted
not only IG, maybe thread in future». Instagram first.

This node replaces [20](20-observe.md) §1's fixed clock for the social stream
(«at least every 12 hours») and makes §2's items concrete. What §2 rules stays:
members only, nothing crawled, the per-person off switch, non-member text
never the sole citation, and the live read verbs withheld from cognition. Play
(Steam, Discord) stays in step 5 of [70](70-migration.md) and is not in this
node.

## 1. What is measured today, and what is not

| fact | source | status |
|---|---|---|
| a member's current story, every frame with `taken_at`, the reel's `expiring_at` | bridge `GET /accounts/{igid}/story` | built and live (P10/P12) |
| when a member's newest story was posted, without opening it | bridge `GET /accounts/{igid}`, `latest_reel_media` (`has_story` is `!= 0`) | built; the field is forwarded raw for exactly this (`reads.go:136`) |
| how many posts and reels a member has | the same profile read, `media_count`, `clips_count` | built |
| one post or reel by shortcode | bridge `GET /posts/{shortcode}` | built |
| **a list of a member's posts or reels** | none: the route returns `501` | **no mechanism** (P13) |
| whether reading a story marks it seen to its author | none | **unmeasured** (instagram-adoption/04, 05 withdrew the probe) |

What the official API can do here, from Meta's documentation and not measured
by this repo: the Instagram Graph API lists only the authenticated
professional account's own media; Business Discovery reads another *business
or creator* account's public media, never a personal account and never
stories; the Basic Display API was shut down on 2024-12-04. Members are
personal accounts, so the official API does not serve this stream.

## 2. One source interface, per network

Each network implements one port; the poller and the Record never name a
network.

```
SocialSource
  network: str                       # "instagram"; "threads" later
  capabilities: frozenset[Cap]       # PROBE, STORY, LIST_ITEMS, READ_ITEM
  probe(account) -> Probe            # cheap: newest-story time, item counts
  story(account) -> StorySnapshot    # frames with taken_at, expires_at
  list_items(account, before) -> Page[ItemRef]   # posts and reels, newest first
  read_item(ref) -> SocialItem
```

A capability a network lacks is absent, not an error: Threads has no stories,
so it never exposes `STORY`, and the poller never schedules a story read for
it. Instagram today exposes `PROBE`, `STORY` and `READ_ITEM`; `LIST_ITEMS`
joins when P13 finds a mechanism (§6). The port sits beside `SocialRouter`
and goes through the same doors: for Instagram, the bridge only
(instagram-adoption/15).

Accounts come from `identity.toml` through the one membership function of
[20](20-observe.md) §2. A member with no account on a network is not polled
there; a member whose off switch is set is not polled anywhere.

## 3. The poll: a cheap probe decides, then the costly read

Every poll of a member is two stages:

1. **Probe** (one profile read). If `latest_reel_media` is newer than the
   newest frame already recorded, a story read is due. If `media_count` or
   `clips_count` grew, a list read is due (once `LIST_ITEMS` exists). If
   neither, the poll ends: nothing was opened.
2. **Read** only what the probe said changed. Each frame or item is keyed by
   `(network, item_id)`; an item already recorded is not written again, and
   only a new item is sent to perception.

The probe makes the common case cost one light call and no vision call, and it
keeps the story read rare, which is what the login risk ([20](20-observe.md)
§2) asks for. A count that fell (a post deleted) records nothing and is
counted (`outcome="count_fell"`); the item, if recorded, stays as it was seen.

## 4. The cadence: per person, learned, and raised by what is happening

A story lives 24 hours, so any interval under 24 hours sees every frame. The
cadence is therefore about **freshness**, how soon after posting Nora knows,
and about spending a fixed budget where it buys the most.

- **Learned rhythm.** Each member has a posting histogram over local hour of
  week, built from the `taken_at` of their recorded items (a Rollup aggregate,
  [25](25-rollup.md), rebuildable). The expected number of new items since the
  last probe, `λ(person, now)`, is read from it.
- **Raised by the Rollup.** An open outing or co-location episode for the
  person, an event of theirs dated today, or a message from them in any of the
  unit's rooms in the last hour multiplies `λ` (starting at 3). People post
  during the things they do; a story about tonight's dinner matters tonight.
- **Priority** of a member is `λ × hours since their last probe`. Each tick
  (starting at every 10 minutes) the poller probes the highest-priority members
  that the budget allows.
- **Bounds.** No member is probed more often than every 30 minutes, and every
  member is probed at least every 6 hours: the floor still sees every frame
  with 18 hours to spare, and the cold start (no history yet) is the floor.
- **Budget.** One per-login hourly cap on bridge calls, probes and reads
  together (starting at 60), with jitter on every call. The budget, not the
  member count, bounds what the login is exposed to; a unit with more members
  gets fresher reads for the active ones, not more calls.
- **Back-off.** A login challenge, a checkpoint, `401` or a rate-limit answer
  stops every social poll for the login (`outcome="halted"`) until the bridge
  reports the session healthy again; it is never retried on the tick.

All starting values are config keys under `[social.poll]`, sized after a week
of `social_poll` rows, never on one sample.

## 5. The Record

Two append-only tables, both hidden on forget and on retention
([70](70-migration.md) §9), never deleted:

- **`social_item`**: `(network, item_id)` unique; `unit`, `person`,
  `account`, `kind` (`story_frame`, `post`, `reel`, later `thread_post`),
  `taken_at`, `expires_at` (story frames), `caption`, `alt_text`, `location`,
  `tagged` and `mentioned` handles (recorded, never read, [20](20-observe.md)
  §2), the media blob refs, `perception_ref` (null until perceived, or past
  the per-unit cap), `seen_at` (when the poller recorded it), `origin`
  (`poll` or `backfill`).
- **`social_poll`**: one row per probe and per read, with `network`,
  `person`, `stage`, `outcome` (`unchanged`, `story_due`, `items_due`,
  `count_fell`, `ok`, `halted`, `error`) and the latency. This is the source
  health of [20](20-observe.md) §3: silence from a person whose polls failed
  is not absence.

Each new item raises one `social` stimulus. A back-filled item raises none
(§6): history is evidence, not news.

The memory index (design/unified-memory) projects `social_item` like any
other store. This node writes the table and nothing in the index; the index
reads it.

## 6. The back-fill of past posts and reels

When a member is first polled on a network, or when `LIST_ITEMS` first
becomes available, the poller pages their posts and reels newest first, up to
a depth limit (starting at 90 days or 50 items, whichever comes first), at the
budget's lowest priority, so it never delays a story read. Items are written
with `origin = backfill`, perceived under the same per-unit cap (back-fill
yields to new items when the cap is near), and raise no stimulus. The
Character adaptor sees them as evidence through its own watermark
([20](20-observe.md) §3, the slow adaptors' wake rules).

**Blocked on P13.** No listing mechanism is known: the Go library's GraphQL
registry has no feed document, OpenCLI's `instagram user` returns the HTML
shell, and the official API does not serve personal accounts (§1). The one
untried path is Instagram's private web API (`/api/v1/feed/user/{id}/`,
`/api/v1/clips/user/`) through the bridge's own session, which already calls
`/api/v1/media/{id}/info/` (instagram-adoption/04). It is probed on its own,
on norty, before anything here depends on it: one member, one page, the
response shape and the session's health afterwards. If it fails, the back-fill
does not ship and §3's probe still records each post's arrival as a count.

## 7. Build order

| step | ships | measured by |
|---|---|---|
| S0 | P13 probe of the private feed route through the bridge session | one page read back on norty, the session healthy an hour later |
| S1 | `SocialSource` port, Instagram adapter (`PROBE`, `STORY`, `READ_ITEM`), `social_item` and `social_poll` tables, the stimulus | unit tests on the port and the dedupe; migration heads |
| S2 | the poller: probe-then-read, fixed floor cadence, budget, halt; the story rule's test rewritten ([20](20-observe.md) §2) | a week of `social_poll` rows; the login unharmed |
| S3 | perception of new frames under the per-unit cap | `nora_perception_total{source="social"}` |
| S4 | the learned cadence and the Rollup raise | median probe-to-`taken_at` lag before and after, from `social_poll` and `social_item` |
| S5 | `LIST_ITEMS` and the back-fill, only if S0 found a mechanism | items per member against `media_count` |

S1 to S3 need no answer from P13. S4 needs a week of S2's rows to learn from.

## 8. Open

- Whether a story read is visible to its author is unmeasured (§1). The
  operator accepted the login risk in [20](20-observe.md) §2; visibility is a
  separate fact, and a measurement of it, if one is found, belongs here.
- Retention keys for `story_frame` and the other kinds are set before S2
  ships ([70](70-migration.md) §9).
