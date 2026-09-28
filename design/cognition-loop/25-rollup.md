---
type: design
title: "Rollup — what the Record adds up to, computed by code"
description: "The tier between the Record and everything a model writes: episodes and aggregates as a pure, replayable projection of the Record up to a logical watermark, generalising the presence analytics substrate (design 27) from presence to every stream. The rollups, their keys and inputs, versioned builds, citation by overlap, the deviation signal, and why no model writes here."
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
and the dashboard queries that. Nora's Record is the lake. Without this tier,
every question about a routine or a relationship would make a model read raw
messages and location fixes at answer time: expensive, unrepeatable, and the
thing that fills prompts.

The Rollup already exists for one stream. The presence analytics substrate
(design/proposed/27-presence-analytics.md, `nora/analytics/rollup.py`) turns
fixes into typed episodes (stays, trips) and aggregates (places, occupancy,
rhythm, corridors), and its §3.5 rule is this tier's rule: typed rows, never
prose, fully deterministic. This node generalises it to every stream.

## 2. The rule: a pure projection

- **No model writes a Rollup row.** A model-extracted claim is how the
  knowledge tier died (#115: 5 rows in 11 days,
  design/dreamer-agent-loop/20-knowledge-tier.md §0). A code aggregate over
  structured signals is what has worked here (design 27).
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
  rebuilt at watermark T from the Record as of T, hidden rows included
  ([10](10-tiers.md) §8).
- **Cited by key, resolved by overlap.** Citations name `(kind, subject,
  interval)`, never a row id or an exact start, because stays are rebuilt by
  truncation (design 27 §1.3). After a rebuild a citation resolves to the rows
  overlapping its interval; if none do, the citing row is `unsupported`
  ([10](10-tiers.md) §2 rule 7). A face co-appearance has no interval of its
  own and is cited by its media item.
- **No upward inputs.** A Rollup is a function of the Record: some rollups
  read other rollups (an outing reads co-locations), never an upper tier (a
  daybook counts outings, not events attended).
- **Fail-closed labelling** (design 27 §3.4): `unlabeled` is an answer, never
  smoothed into a guess.

## 3. The rollups

**Episodes** — one bounded happening, with a start, an end, participants.

| rollup | question | key | built from | exists |
|---|---|---|---|---|
| **stay**, **trip** | where was X, from when to when? | (person, interval) | presence fixes and polls | yes (design 27's Tier 1) |
| **co-location** | who was with whom, where, when? | (unit, person set, place, interval) | overlapping stays within a radius | design 27's Tier 6, speculative → built here |
| **face co-appearance** | who appeared together in one media item? | (unit, person set, item); cited by item | face matches in one item ([60](60-faces.md)); no place of its own. Every face places a person, so its lineage is `presence` for each person in it, and every entry must pass ([10](10-tiers.md) §3) | new |
| **outing** | where have we been together, how often? | (unit, place, interval) | co-locations of ≥ 2 members away from any member's home | new |
| **play session** | who played what, with whom, when? | (unit, game, interval) | play samples (§6) | new |
| **daybook** | what did X do on day D? | (unit, person, local date) | that day's stays, co-locations, outings, play sessions, rooms active in, social items posted | new |

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
(reactions at step 1, social and play at step 5, faces at step 6).

## 4. Signals the Rollup raises

A Rollup row is observed state changing, so it can wake cognition
([20](20-observe.md) §3):

- **co-location start** — two or more members at one place now.
- **play together** — a play session with two or more members starts.
- **deviation** — a person's pattern broke: a rhythm anomaly, or a daybook
  silent in every room and at home for N days. It is the only way *absence*
  wakes the loop, and it is raised only when every source it rests on is
  proven healthy for the interval ([20](20-observe.md) §3). It is a signal,
  not a rule: what to make of it is her judgement ([30](30-cognition.md) §3).

## 5. How it is read

- Never pushed whole into a prompt. Read with `rollup_read(kind, subject,
  interval)` ([30](30-cognition.md) §2), under lineage ([10](10-tiers.md) §3).
- `where_been` and `routine_read` (DEC-215, the rhythm's forecast) stay the
  tag path's presence readers. For the Dreamer's adaptors only, under lineage,
  `rollup_read` supersedes `where_been`'s 90-day cap (`_been_max_days`,
  `nora/agent/tools.py`, clamped again in the store,
  `nora/infra/sql/mobility.py`) and DEC-155's rule that the Dreamer is not granted
  `where_been` ("spec §7.4" in `nora/agent/tools.py`).
- `route` refuses coordinates taken from a presence rollup (R-28).
- Cited by the tiers above: an event's `Happened` section cites the outing; a
  Character line about routine or closeness cites the rhythm or the tie
  ([10](10-tiers.md) §2 rule 5).

## 6. Play sources

| source | gives | status |
|---|---|---|
| Steam Web API | the current game only (`GetPlayerSummaries`, `nora/infra/steam.py`), for public profiles only; a play session is built from polling each member every 1–5 minutes | live reads exist; polling into the Record is new |
| Discord | rich presence (the game a member is in) from `nora/platform/discord/discord_presence.py`, on the `presences` intent `nora/platform/discord/adapter.py` already enables; voice occupancy from `voice_presence.py` | both exist as live reads; reused and sampled into the Record |
| game-specific (later) | CS2 stats (Steam or third-party), Valorant (Riot API: production key, per-player consent), Apex (third-party) | later; each a source feeding play samples, never a tier |
