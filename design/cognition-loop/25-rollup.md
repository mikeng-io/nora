---
type: design
title: "Rollup — what the Record adds up to, computed by code"
description: "The layer between the Record and everything a model writes (a layer, not a tier: 05-data-model.md, DEC-249): episodes and aggregates as a pure, replayable projection of the Record up to a logical watermark, generalising the presence analytics substrate (design 27) from presence to every stream. The rollups, their keys and inputs, versioned builds, citation by overlap, the deviation stimulus, and why no model writes here."
tags: [nora, rollup, episode, aggregate, presence-analytics, co-location, outing, daybook, tie, play]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: depends_on, provenance: asserted}
  - {dst: ../proposed/27-presence-analytics.md, rel: generalises, provenance: asserted}
---

# 25 · Rollup

## 1. Why it exists

The operator's analogy (2026-09-28): nobody runs analytics live on the
dashboard; data lands in a lake, is transformed and loaded into a warehouse,
and the dashboard queries that. Nora's Record is the lake. (Those are the
analogy's words; the layer names are Record, Rollup, Notebook and Views,
[05](05-data-model.md) §1.) Without this layer,
every question about a routine or a relationship would make a model read raw
messages and location fixes at answer time: expensive, unrepeatable, and the
thing that fills prompts.

The Rollup already exists for one stream. The presence analytics substrate
(design/proposed/27-presence-analytics.md, `nora/analytics/rollup.py`) turns
fixes into typed episodes (stays, trips) and aggregates (places, occupancy,
rhythm, corridors), and its §3.5 rule is this layer's rule: typed rows, never
prose, fully deterministic. This node generalises it to every stream.

## 2. The rule: a pure projection

- **No model writes a Rollup row.** A model-extracted claim is how the
  knowledge tier died (#115: 5 rows in 11 days,
  design/dreamer-agent-loop/20-knowledge-tier.md §0). A code aggregate over
  structured stimuli is what has worked here (design 27).
- **A function of the Record and a logical watermark.** Every row is what one
  function computes from the Record up to a watermark (the newest Record
  item it has read), so the incremental path and a batch rebuild are the same
  function and must agree exactly. **No wall-clock gate**: time advances only
  through the Record. A stay closes when a later fix, or a recorded collector
  poll, shows it ended, never because a clock passed. Design 27's incremental
  path does read a wall clock in its close gate and measured within 3.4% of
  its batch path (design 27, the 2026-09-17 re-derivation); step 2 moves it
  onto the watermark, and until then the nightly rebuild diff
  ([30](30-cognition.md) §6) is its bound and is reported.
- **Versioned builds, kept.** Every row carries the `build` that computed it
  and its `recorded_at`. A new build (a code change, or a new input stream
  arriving) rebuilds into a new version and switches atomically; the build it
  replaces is superseded, not deleted, which ends design 27's rebuild by
  truncation. The Rollup as of T is the build current at T; for eval it is
  rebuilt at watermark T from the Record as of T, rows hidden after T included
  ([10](10-tiers.md) §8).
- **Cited by key, resolved by overlap.** Citations name `(kind, subject,
  interval)`, never a row id or an exact start, because a new build
  recomputes rows and their boundaries. After a rebuild a citation resolves to the rows
  overlapping its interval; if none do, the citing row is `unsupported`
  ([10](10-tiers.md) §2 rule 7). A face co-appearance has no interval of its
  own and is cited by its media item.
- **No upward inputs.** A Rollup is a function of the Record: some rollups
  read other rollups (an outing reads co-locations), never the Notebook (a
  daybook counts outings, not events attended).
- **Fail-closed labelling** (design 27 §3.4): `unlabeled` is an answer, never
  smoothed into a guess.

**As built (step 2a, 2026-09-29, DEC-235).** The details this section left
open, as the build fixed them:

- **The watermark and the clock.** The presence fold reads one person's
  Record events — their admitted fixes and the collector's *successful*
  polls of their circles (`source_health`) — in one canonical order,
  `(recorded_at, source, id)`, one event at a time
  (`nora/analytics/rollup.py`, `MobilityRollup._step`). Its clock is the
  newest time those events show (a fix's `ts`, a poll's `at_ms`); a failed
  poll is no evidence of time (R-27). The wall clock chooses only *which*
  watermark a run builds to (`now - grace_seconds`, so a row committed a
  moment late is inside it); it never enters what is computed there.
  Because a rebuild is the same fold from the start, the two agree by
  construction; `tests/unit/test_rollup_determinism.py` and
  `tests/integration/test_rollup_builds.py` are the gate.
- **Design 27's 3.4%, its cause.** The old incremental path was handed the
  daemon's wall clock and decided every tail with it (an open stay closed at
  `DWELL_CAP_MS` of wall time, a trip settled at `settled_seconds`, an
  arrival held on the clock, a label expired at `label_freshness_days`); a
  batch evaluated the same rules once, long after every fix, so a tail
  inside the history was decided by the next fix. The quiet phone at home is
  the case: one stay in the batch, two tick by tick. On the fold, a stay
  closes on the clock only when a recorded poll proves the time passed, on
  every schedule alike. Two further history dependences went with it: a
  place's name came only from the stays whose fixes the current cycle saw
  (stays now keep their mode address), and a corridor's trips depended on
  the order they arrived in (corridors are now recomputed from the trips).
- **Builds.** One build covers every rollup (`rollup_build`; the pointer is
  its one `current` row). A build is named by `CODE_VERSION` and a digest of
  the parameters a row depends on; a start on other code or parameters
  rebuilds from the Record and switches atomically, and `python -m
  nora.analytics rebuild` does so on demand. The rows written before 0071
  are the `legacy` build. Rows of the current build change in place (a
  relink, a label, a diffed unit rollup) and keep `row_history`, so the
  Rollup as of T is the build current at T with its rows as they were at T.
- **Inputs that are not events.** Saved places and the geocode cache are
  read as the store holds them when a run reads them; the place plan runs
  again when the saved places change. A rebuild as of a past T reads them
  from a snapshot at T (70 §2), where they are restored as of T.
- **Keys and columns.** Stay, trip, place, occupancy, rhythm and corridor
  rows carry `build_id`, `lineage` (`[["presence", <person>, null]]`) and
  `recorded_at`; the unit rollups carry `unit`, their natural key, `lineage`,
  `valid_from_ms`/`valid_to_ms` and `as_of_ms` (the unit's Record clock).
  `reasoning` is `computed` for every Rollup row by definition and is not a
  column. Surrogate ids are stable within a build (by overlap, as places
  always were) and differ between builds; `SqlRollupStore.resolve` answers a
  `(kind, subject, interval)` citation by overlap against the current build.
  The lineage format is the store's one format since step 2's lineage half
  (DEC-236, [10](10-tiers.md) §3 «As built»): notes, Standing and culture
  store the same triples, a Rollup row's array read as the row's one set.

## 3. The rollups

**Episodes** — one bounded happening, with a start, an end, participants.

| rollup | question | key | built from | exists |
|---|---|---|---|---|
| **stay**, **trip** | where was X, from when to when? | (person, interval) | presence fixes and polls | yes (design 27's Tier 1) |
| **co-location** | who was with whom, where, when? | (unit, person set, place, interval) | overlapping stays within a radius | design 27's Tier 6, speculative → built here |
| **face co-appearance** | who appeared together in one media item? | (unit, person set, item); cited by item | face matches in one item ([60](60-faces.md)); no place of its own. Every face places a person, so its lineage is `presence` for each person in it, and every triple must pass ([10](10-tiers.md) §3) | new |
| **outing** | where have we been together, how often? | (unit, place, interval) | co-locations of ≥ 2 members away from any member's home | new |
| **play session** | who played what, with whom, when? | (unit, game, interval) | play samples (§6) | new |
| **daybook** | what did X do on day D? | (unit, person, local date) | that day's stays, co-locations, outings, play sessions, rooms active in, social items posted | new |
| **response** | how did people respond to something she sent? | (unit, her send's Record id) | replies, reactions and edits to that send, and unquoted `follow`s (§3 as built), within `response_window_seconds` (starting at 3600 s); `silent` when a send whose Record row carries the `ask` intent of its brief, or one naming a person, got nothing within the window | new; the outcomes the Lessons adaptor and Character · Nora read |

**Aggregates** — counts, patterns and clusters over a window.

| rollup | question | key | built from | exists |
|---|---|---|---|---|
| **place**, **occupancy**, **rhythm**, **corridor** (with its trip speeds) | where does X usually go, when, by which way? | (person, place or hour-of-week or corridor) | stays, trips | yes (design 27's Tiers 2, 3, 5). Anomalies are computed on read (`nora/domain/mobility_forecast.py`), not stored |
| **tie** | how much do A and B interact, and is it changing? | (unit, pair, ISO week) | replies, mentions, reactions, co-tags, co-location minutes, play sessions together | new |
| **play profile** | what does X play, how much, with whom? | (unit, person, ISO week) | play sessions | new |

Stay, trip, place, occupancy, rhythm and corridor are **person-scoped**: keyed
by person and shared across that person's units ([10](10-tiers.md) §2 rule
6). The rest are keyed by unit.

**Partial first.** Daybook and tie ship at step 2 from messages and presence
alone, and are rebuilt as a new build when each further input arrives
(reactions at step 1, social at step 5 (built: the daybook), play at step 5, faces at step 6).

**As built (step 2a).** Co-location: two members' stays (closed, or in
progress once past `min_dwell`) within `colocation_radius_m` (150 m)
overlapping at least `colocation_min_overlap_seconds` (600 s); stays joined
that way, transitively, are one episode. **Outing, the `unlabeled` home
case:** a member with no place labelled home in the build cannot be proved
away from it, so the co-location is not an outing and names that member in
`homes_unknown`; a co-location within a member's home place is a visit
(`at_home_of`), not an outing. Daybook: per member per local date, stays
split at midnight (home minutes from places labelled home), co-location
minutes and companions, outings, messages and reactions per room;
`items_posted` counts the member's social items by the local date of their
`taken_at` (posts, reels and story frames, back-filled items in their own past
day; a gone or hidden item and every item of a person with `social = false`
are left out at the read, [22](22-social-stream.md) §6). A day with none is `0`
once the member's accounts have been observed (their first answered
`social_poll` is on or before that date) and NULL before it, for a member with
no account on any network and for one who opted out: NULL is «not an input»,
`0` is «looked, none». A row that carries a count cites `social` lineage for
the member. The count is part of the recompute every run does over the whole
Record up to the watermark, so a back-filled item recorded later reaches its
day on the next run, and the daybook rows of a build at a watermark are the
same however the runs fell; there is no late-arrival window to widen. Tie: per
pair per ISO week, replies (a quote of the other's message), mentions (a
structured mention resolving to the other), reactions, each direction, and
co-location minutes split at the week boundary. Response: per send,
replies, reactions and edits of a reply inside `response_window_seconds`, and
unquoted follows (below); `open` while the window runs past the unit's Record clock; with nothing in
a closed window, `silent` only when an answer was expected AND the room's
platform was proved up for the window (`source_health` transitions),
`unknown` when expected but unproved, `unanswered` when nothing was
expected. **Expected** is a structured mention of a member today; the
brief's `ask` intent is read from the send's own Record row: a fire turn's
Deliverer writes `intent` (`tell`/`ask`) into the `raw_json` of every message
it sends (self-growth L0, 2026-10-07). `intent` is NULL for a tag reply (it has
no brief), for a send before L0, and for a proactive row whose payload holds
free text instead of `tell`/`ask`.

**Unquoted answers (`follow`, 2026-10-07).** Most people in a group answer
without quoting her, so a quote-only response left answered sends `unanswered`
(and an `ask` `silent`). A member's message in the same room counts as a
`follow` of her send when it quotes none of her sends and either (a) comes
within `follow_window_seconds` (120 s) of her latest send in that room, carries
no structured mention of anyone but her and does not quote anyone else's
message (a quote of another member is a reply to that member, like a mention of
them), or (b) at any time before the
response window closes names her: a structured mention resolving to her own
account in that room, or one of `follow_names` (`nora`) as a whole word,
case-insensitive, in the body (`nora.tag.is_tagged`'s rules). It counts for ONE
send: the last send of the turn (`tag_id`, as L0 reads it; a send with no
`tag_id` is its own turn) that delivered her latest send before it, so a
message after her next, different-turn send attaches to that one, and a
multi-part turn is answered at its last part (a message between the parts is
stamped at that last part). A message that quotes her is a `reply`, counted
once. A `follow` is an `outcome = responded` like any other and is kept apart:
`rollup_response.follows` counts them, `replies` stays «quoted it», and
`responders` holds both. **Her account ids** come from the Record, not from a
platform registry (the analytics process has none): the `sender_id` her own
rows in the unit's rooms were written under (`PlatformIdentity.primary` at
send time). A mention of her under an id form that never wrote a row (a Signal
ACI alone) does not resolve; the message is then read by what it says, and a
message that mentions only her under such an id reads as mentioning someone
else. The rule is a measurement, not a law: 14 days of the friends unit and 60
labelled follow-up messages, one labeller; inside 120 s without a mention of
anyone else 33 answered her, 3 did not and 11 were ambiguous (design
self-growth/40 §6). Changing it bumps `CODE_VERSION` (`rollup-2a.3`) and the
parameters hash, so the next start rebuilds.

## 4. Stimuli the Rollup raises

A Rollup row is observed state changing, so it is put in front of cognition
([20](20-observe.md) §3). **Since DEC-279 (2026-10-03) it does not wake a
session by itself**: it rides the next wake for any reason, the daily floors
included, because six of one unit's nine sessions in a day were a lone
deviation closed `no_change`. A `deviation_anomaly` is shown with the stay's
place and what is usual for the person there, read from the presence store
at grounding time ([30](30-cognition.md) §1):

- **co-location start** — two or more members at one place now.
- **arrival at an event's place** — a stay starts at a place an open event
  names.
- **play together** — a play session with two or more members starts.
- **deviation** — a person's pattern broke: a rhythm anomaly, or a daybook
  silent in every room and at home for N days. It is the only way *absence*
  wakes the loop, and it is raised only when every source it rests on is
  proven healthy for the interval ([20](20-observe.md) §3). It is a stimulus,
  not a rule: what to make of it is her judgement ([30](30-cognition.md) §3).

**As built (step 2a).** Every Rollup stimulus is `kind = 'rollup'` in the
per-unit `stimulus` table, unique on `(unit, ref)` (migration 0071), where
`ref` names the row that raised it — so a later run, or a rebuild
recomputing the row, never raises it twice — and only for a row whose start
is within `stimulus_horizon_seconds` of the unit's Record clock (a rebuild
over months raises nothing historical). Co-location start: a co-location
still open. Arrival: a member's stay in progress inside a place an open
event names — `EventPlaces` is the hook; events are step 2b's and name no
place yet, so none is raised. Deviation: a newly closed stay the rhythm
scores anomalous, or `deviation_silence_days` completed days on which the
member posted and reacted nowhere in the unit and was never at home;
**healthy** is proved from `source_health`: the collector polled the
person's circles at most `health_max_poll_gap_seconds` (900 s) apart across
the span with every poll `ok`, and, for silence, every platform of the
unit's rooms was `up` throughout. Withheld deviations are counted
(`nora_rollup_stimuli_total{outcome="unhealthy"}`).

## 5. How it is read

- Never pushed whole into a prompt. Read with `rollup_read(kind, subject,
  interval)` ([30](30-cognition.md) §2), under lineage ([10](10-tiers.md) §3).
- `where_been` and `routine_read` (DEC-215, the rhythm's forecast) stay the
  tag path's presence readers, and `rollup_read` joins them (DEC-312, «As
  built» below). For the Dreamer's adaptors only, under lineage,
  `rollup_read` supersedes `where_been`'s 90-day cap (`_been_max_days`,
  `nora/agent/tools.py`, clamped again in the store,
  `nora/infra/sql/mobility.py`) and DEC-155's rule that the Dreamer is not granted
  `where_been` ("spec §7.4" in `nora/agent/tools.py`).
- `route` refuses coordinates taken from a presence rollup (R-28).
- Cited by the Notebook's tiers: an event's `Happened` section cites the outing; a
  Character line about routine or closeness cites the rhythm or the tie
  ([10](10-tiers.md) §2 rule 5).

**As built (step 3, PR B, DEC-246).** `rollup_read` reads the current build
for the calling room's unit (`nora/infra/sql/rollup_read.py`) and renders
each row with its citation key, a `through` stamp and no coordinate
(`nora/agent/rollup_read.py`). Kinds: stay, trip, daybook, colocation,
outing, tie; the forecasting aggregates stay behind `routine_read` and
`response` is the Lessons adaptor's. A person resolves among the calling
room's people; every row passes lineage for that room or is withheld and
counted. A hidden message stops contributing when its row is next
recomputed, not before, which `through` makes visible.

**Amended 2026-10-15 (DEC-312): a person's tag turn reads it too.** Until
then the tool was Dreamer-only and the tag path kept `where_been`'s 90 days.
The unified-memory step 0 journeys
([unified-memory/00](../unified-memory/00-intent.md) «Sequence») showed the
cost: asked where a group had been together lately, she rebuilt outings from
four transcript loads, and for a week with none she placed an older outing in
it. `rollup_read` is now offered on a tag turn (`nora/agent/surface.py`
`TAG_READABLE_LOOP_TOOLS`), with the same lineage for the bound room. It is
still not a fire turn's, a sub-agent worker's or a Dreamer adaptor's, and
`where_been` and `routine_read` keep their own questions (one person's day,
usual places, the rhythm's forecast).

## 6. Play sources

| source | gives | status |
|---|---|---|
| Steam Web API | the current game only (`GetPlayerSummaries`, `nora/infra/steam.py`), for public profiles only; a play session is built from polling each member every 1–5 minutes | live reads exist; polling into the Record is new |
| Discord | rich presence (the game a member is in) from `nora/platform/discord/discord_presence.py`, on the `presences` intent `nora/platform/discord/adapter.py` already enables; voice occupancy from `voice_presence.py` | both exist as live reads; reused and sampled into the Record |
| game-specific (later) | CS2 stats (Steam or third-party), Valorant (Riot API: production key, per-player consent), Apex (third-party) | later; each a source feeding play samples, never a tier |
