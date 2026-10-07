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

Not yet measured.
