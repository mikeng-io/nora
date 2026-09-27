---
type: design
title: "Rollup — what the Record adds up to, computed by code"
description: "The tier between the Record and everything a model writes: episodes and aggregates, deterministic and recomputable, generalising the presence analytics substrate (design 27) from presence to every stream. The rollups, their keys and inputs, the deviation signal, stable citation keys, and why no model writes here."
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
fixes into typed episodes (stays, trips, places) and aggregates (rhythm,
anomaly, routes), and its §3.5 rule is this tier's rule: **it emits facts,
never prose, and it is 100% deterministic.** This node generalises it to every
stream.

## 2. The rule: code only

- **No model writes a Rollup row.** A model-extracted "fact" is how the
  knowledge tier died (#115: 5 facts in 11 days,
  design/dreamer-agent-loop/20-knowledge-tier.md §0). A code aggregate over
  structured signals is what has worked here (design 27).
- **Recomputable.** Every row can be rebuilt from the Record; a rebuild that
  differs from the stored row is a data-quality failure ([70](70-migration.md) §4).
- **Fail-closed labelling** (27 §3.4): `unlabeled` is an answer, never
  smoothed into a guess.
- **Stable citation keys.** Stays are rebuilt by truncation (27 §1.3), so a
  row id can vanish. Rollup rows are cited by a natural key — `(kind,
  subject, place_ref, start)` or `(kind, subject, span)` — that survives a
  rebuild, never by a surrogate id.

## 3. The rollups

**Episodes** — one bounded happening, with a start, an end, participants.

| rollup | question | key | built from | exists |
|---|---|---|---|---|
| **stay**, **trip** | where was X, from when to when? | (subject, start) | presence fixes | yes (27 Tier 1) |
| **co-location** | who was with whom, where, when? | (unit, person pair, place_ref, start) | overlapping stays within a radius; face matches in one item | 27 Tier 6, speculative → built here |
| **outing** | where have we been together, how often? | (unit, place_ref, start) | co-locations of ≥ 2 members away from any member's home | new |
| **play session** | who played what, with whom, when? | (unit, game, start) | play samples: Steam in-game state, Discord voice occupancy and activity | new |
| **daybook** | what did X do on day D? | (unit, person, local date) | that day's stays, co-locations, play sessions, rooms active in, social items posted, events attended | new |

**Aggregates** — counts and patterns over a window.

| rollup | question | key | built from | exists |
|---|---|---|---|---|
| **place**, **rhythm**, **anomaly**, **route** | where does X usually go, when; is today unusual? | (subject, place / hour-of-week) | stays, trips | yes (27 Tiers 2, 3, 5) |
| **tie** | how much do A and B interact, and is it changing? | (unit, pair, ISO week) | replies, mentions, reactions, co-tags, co-location minutes, play sessions together, follows | new |
| **play profile** | what does X play, how much, with whom? | (unit, person, ISO week) | play sessions | new |

## 4. Signals the Rollup raises

A Rollup row is observed state changing, so it can wake cognition
([20](20-observe.md) §3):

- **co-location start** — two or more members at one place now. The loop may
  confirm an event, or propose one nobody arranged.
- **deviation** — a person's pattern broke: the rhythm anomaly fires, or a
  daybook shows a member silent in every room and at home for N days. This is
  the only way *absence* wakes the loop; absence ingests nothing.
- **play together** — a play session with two or more members starts.

## 5. How it is read

- Never pushed whole into a prompt. Read with `rollup_read(kind, subject,
  span)` ([30](30-cognition.md) §2), under lineage ([10](10-tiers.md) §3).
- `where_been` stays the tag path's presence reader; `rollup_read` supersedes
  its 90-day cap (`_been_max_days` in `nora/agent/tools.py`) and design 27
  §7.4's rule that the Dreamer never reads it, for cognition only and under
  lineage.
- Cited by the tiers above: an event's `Happened` section cites the outing; a
  Character line about routine or closeness cites the rhythm or the tie
  ([10](10-tiers.md) §2 rule 5).

## 6. Play sources

| source | gives | status |
|---|---|---|
| Steam Web API | in-game state, current game (`nora/infra/steam.py`, live today), recent playtime, owned games | live reads exist; sampling into the Record is new |
| Discord | voice-channel occupancy; "playing X" activity (needs the presence intent on the bot) | voice seen today; activity new |
| game-specific (later) | CS2 stats (Steam or third-party), Valorant (Riot API: production key, per-player consent), Apex (third-party) | later; each a source feeding play samples, never a tier |
