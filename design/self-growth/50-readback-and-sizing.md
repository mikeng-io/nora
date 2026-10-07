---
type: design
title: "Read-back and sizing: the numbers that set every threshold, measured before code"
description: "Before C1 and L2, read back from the deploy host how much evidence each entity and unit produces per day and what one culture pass costs today. Those numbers set character_batch per kind, character_stale_seconds, lessons_batch and the cost per day of many small sessions."
tags: [nora, readback, sizing, langfuse, postgres]
timestamp: '2026-10-07'
edges:
  - {dst: 10-character, rel: informs, provenance: asserted}
  - {dst: 40-lessons, rel: informs, provenance: asserted}
---

# 50 · Read-back and sizing

Done with `nora-readback` on the deploy host, over the last 14 days, per unit.
Nothing in [10](10-character.md) or [40](40-lessons.md) is sized until these
are in this node with their window and n.

## 1. What to read

| number | source | sets |
|---|---|---|
| messages per person per day (median, p90) | Postgres, the Record | `character_batch` for persons |
| messages per room per day | Postgres | `character_batch` for rooms |
| her sends per unit per day | Postgres | expected `response` episodes |
| closed `response` episodes per unit per day, by outcome (with replies or reactions, `silent`, `unknown`, `unanswered`) | Postgres, the Rollup | `lessons_batch`; how often `silent` is real |
| culture passes per day, tokens and model calls per pass, tool calls per pass | Langfuse, culture adaptor traces | cost of one Character session |
| `memory.chat` latency and calls per pass | Langfuse | whether `<people_views>` stays |

## 2. The arithmetic

- Sessions per day per unit ≈ Σ over entities (evidence per day ÷ batch) plus
  stale wakes. With the prefix cached, cost per session is mostly the
  evidence. Compare against today's one pass per room per day.
- If a unit closes about 5 response episodes a day, `lessons_batch = 30` means
  a lesson pass about once a week. The operator decides whether that is right.

## 3. Results

Read 2026-10-07 on the deploy host. **Window: 14 HKT days, 2026-09-23 00:00
to 2026-10-07 00:00** (Postgres). Langfuse: `dream.pass` traces from
2026-09-22T16:00Z to 2026-10-06T16:00Z; the last one in it is 08:01Z on
10-06. Rooms map to units by the live `policy.toml`. Hidden messages are
excluded; 2 messages came from rooms not in the policy and are left out.
Day counts include zero days. Persons are labelled by rank, not named.

### 3.1 Messages per room (people only, not hers)

| unit | room | total | per day mean | median | p90 | max | active days |
|---|---|---|---|---|---|---|---|
| friends | Friends (Signal) | 691 | 49.4 | 38.5 | 93.7 | 158 | 14 |
| friends | Zone 42 (Discord) | 470 | 33.6 | 30 | 57.7 | 61 | 14 |
| friends | ~67~ (Instagram) | 87 | 6.2 | 6.5 | 10.7 | 11 | 14 |
| friends | WhatsApp Friends | 19 | 1.4 | 0 | 3.8 | 13 | 3 |
| ridgeline | Ridgeline (Signal) | 62 | 4.4 | 2 | 12.4 | 15 | 9 |
| ridgeline | Instagram Ridgeline | 37 | 2.6 | 0 | 5.1 | 27 | 4 |
| ridgeline | Ridgeline #discussion (test) | 8 | 0.6 | 0 | 0.7 | 7 | 2 |
| wedding | 11月結婚兄弟團 | 331 | 23.6 | 4 | 91.1 | 106 | 8 |

Per unit: friends 1,267 (median 79.5/day, p90 136.5), wedding 331 (median 4,
p90 91: bursty), ridgeline 107 (median 4).

### 3.2 Messages per person, unit-scoped

| unit | person | 14 days | per day mean | median day | p90 day | active days |
|---|---|---|---|---|---|---|
| friends | P1 | 365 | 26.1 | 24.5 | 41.3 | 14 |
| friends | P2 | 264 | 18.9 | 16 | 35.4 | 14 |
| friends | P3 | 261 | 18.6 | 15 | 35.2 | 14 |
| friends | P4 | 172 | 12.3 | 10.5 | 24.5 | 14 |
| friends | P5 | 134 | 9.6 | 8 | 13.4 | 14 |
| friends | P6 | 64 | 4.6 | 3.5 | 9.4 | 12 |
| friends | P7 | 7 | 0.5 | 0 | 2 | 4 |
| ridgeline | P1 | 107 | 7.6 | 4 | 12.4 | 9 |
| wedding | 5 people (P5, P4, P1, P3, P2) | 80, 77, 69, 59, 46 | 3.3–5.7 | 0–1 | 12–17 | 5–8 |

7 of the friends unit's 10 listed people posted. No human message lacked a
`person_id`.

### 3.3 Her sends and their response episodes

Rollup build 3 (current, through 2026-10-07 12:21 HKT). Every send in the
window has an episode (260 of 260); none is still `open`.

| unit | her sends | per day mean | median | p90 | max | `responded` | `unanswered` | `silent` | `unknown` |
|---|---|---|---|---|---|---|---|---|---|
| friends | 154 | 11.0 | 8.5 | 22.7 | 25 | 34 (22%) | 120 | 0 | 0 |
| ridgeline | 96 | 6.9 | 3.5 | 12.4 | 42 | 14 (15%) | 82 | 0 | 0 |
| wedding | 10 | 0.7 | 0 | 1 | 7 | 3 | 7 | 0 | 0 |

**`silent` and `unknown` never occur.** `intent` is NULL on all 1,098
episodes ever built, and in 14 days no send of hers carried a structured
mention of a member, so nothing was ever "expected" and every unresponded send
is `unanswered`. Today the Lessons evidence is binary: about one send in five
draws a reply, reaction or edit, and the rest say nothing either way. L0
(record the brief's `ask`) is the only way `silent` becomes evidence.

### 3.4 What one culture pass costs today

Langfuse, each `invoke_agent dreamer:culture` agent and the observations under
it; n = 69 runs over 14 days (4.9 a day), all on the `nora-dreamer` alias.

| per run | median | p90 | max | 14-day total |
|---|---|---|---|---|
| model calls | 1 | 2 | 4 | 88 |
| input tokens | 8,290 | 17,447 | 53,868 | 785,362 |
| output tokens | 2,850 | 7,273 | 15,235 | 260,582 |
| cache-read tokens | 384 | 7,808 | 30,592 | 242,614 (31% of input) |
| tool calls | 0 | 2 | 11 | 46 |
| agent latency, s | 14.0 | 34.3 | 83.1 | |

- `cache_read_input_tokens` is absent on 35 of 88 generations, so 31% is a
  floor; absent is not 0.
- Tools used, 14 days: `lookup_person` 25, `group_members` 10,
  `search_memory` 5, `about` 4, `where_been` 1, `ask_memory` 1. **The median
  run calls no tool.** Today's "agent loop" mostly writes from what it was
  handed.
- **`memory.chat` is not visible in Langfuse**: the pre-fetch runs outside
  the agent and Honcho calls are plumbing. Its count is the person revisions:
  59 since DEC-278 landed on 2026-10-03 (about 12 a day), each one answer.
  Latency is unmeasured from traces; the two culture runs since the
  2026-10-06 restart took 60–120 s end to end
  (`nora_dream_run_seconds`) against a 14 s median agent span, so the
  pre-fetch may be most of the wall time (n = 2, not a measurement).
- Culture revisions written per HKT day: 4–8 before 2026-10-03, 11–19 after
  (person accounts).

## 4. Proposed thresholds

Each key wakes on the first of: new evidence ≥ batch, or account older than
7 days with any new evidence. The arithmetic uses §3's 14-day rates.

| key | batch | wakes in practice | sessions a day |
|---|---|---|---|
| room | **200 messages** (the cognition design's value) | Friends ~4 days, Zone 42 ~6, wedding ~8 (sooner on a burst day); every other room by the 7-day stale wake | friends ~0.7, ridgeline ~0.4, wedding ~0.15 |
| person | **100 messages** | P1 ~4 days, P2/P3 ~5; everyone else weekly | friends ~1.2, wedding ~0.7, ridgeline ~0.15 |
| unit | **5 new room revisions** | about weekly in friends; Register rows not measured here | ~0.15 each |
| Nora | **100 closed episodes** | friends ~9 days, so in practice weekly everywhere by the stale wake | ~0.15 each |
| Lessons (unit) | **50 closed episodes** | friends ~4.5 days, ridgeline ~7, wedding weekly by stale | ~0.5 total |

- **Character, all units: about 4.2 sessions a day against today's 4.9
  culture runs; Lessons adds about 0.5.** If a session costs what a room pass costs now (median 8.3k
  in, 2.9k out), the token bill stays flat. A person session reads one
  person's messages, so it should be smaller; that is a guess until C2 runs.
- **Why 200 is wrong for persons.** At 200 the top poster wakes every ~8
  days and everyone else never does by count: the stale wake decides
  everything, which is a weekly clock under another name. 100 is the value
  at which the count, not the clock, drives the heavy posters.
- **Why 50, not 30, for Lessons.** With one send in five drawing any
  response, 30 episodes hold about 6 that say anything; 50 hold about 10.
  At 30, friends would run a lesson pass every 2.7 days on 6 data points.
  Revisit after L0: once `silent` exists, the informative fraction rises.
- **The stale wake revises off very little.** P7 posted 7 messages in 14
  days, so their account would be rewritten weekly from 3 or 4 messages.
  Left as decided (any new evidence); a floor of a few items is the upgrade
  if those revisions prove to be noise.
- **The room cadence slows.** Today a room with any new message folds daily;
  under 200, only Friends folds more than weekly. Room culture is the
  slowest thing here, so this is the intent, but the participation turn
  reads `<culture>` and will see an older account.
