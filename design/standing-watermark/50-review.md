---
type: design
title: "Review — audit findings and operator decisions"
description: "The independent review's findings, the operator's simplifications, and the decisions that close them."
tags: [nora, standing, watermark, review, decisions]
timestamp: 2026-09-01
edges:
  - {dst: 00-intent, rel: depends_on, provenance: asserted}
  - {dst: 10-event-trigger, rel: depends_on, provenance: asserted}
  - {dst: 20-watermark-read, rel: depends_on, provenance: asserted}
  - {dst: 30-rollup, rel: depends_on, provenance: asserted}
---

# Review

## The audit

An independent review of this bundle (delegated, 2026-09-01) found the
direction sound and the falsifier observable, with two implementation
blind spots and one framing error. The operator then simplified the design
further — several "decisions" the review raised were not decisions at all
once the accumulator model was stated plainly.

## Findings and how they closed

### 1. Serialization — the accumulator IS the mechanism (MAJOR → closed)

The review asked how concurrent folds on one conversation are serialized.
The operator's answer: the accumulator is the serialization. A message
arrives, the count since the watermark is checked, and only when the count
reaches the cap does a fold enqueue. The existing `ConversationSlots`
(`nora/engagement/slots.py`, R-10 lock + R-30 FIFO) then guarantees one
fold in flight per conversation; a burst's stragglers are re-counted after
each fold because the watermark advanced. No new machinery. Recorded in
10-event-trigger §Serialization.

### 2. Rollup vs fold_count (MAJOR → closed)

The review worried a long backlog rollup would trip the ceiling trigger
(`rebuild_after_folds = 20`). The operator's model makes the answer
trivial: rollup folds do not increment `fold_count` — that counter stays a
tag-path counter feeding the ceiling. Rollup gets its own counter
(`nora_standing_rollup_folds_total`). Recorded in 30-rollup.

### 3. Framing — the fold already sees Nora's rows (MAJOR → fixed)

The review caught a wrong claim in 20-watermark-read: the current fold
reads `ctx.transcript` = `RecordStore.recent()`, which does NOT filter
`is_nora` — the fold already sees Nora's own rows. The real reason a new
read is needed is that the watermark requires a CURSOR read (`since`-style,
`id > after_id`), and the existing `since` excludes Nora's rows. The new
read is `since_including_nora`: cursor semantics + Nora's rows. Framing
corrected in 20-watermark-read.

### 4. Verdict call input (MINOR → closed)

The verdict call reads the SAME batch as the standing call — it judges the
whole stretch's sourcing, and the two calls must not disagree about what
happened. This is the existing split-fold shape (one gather, shared turn
block); the watermark batch replaces the window for both.

### 5. Backfill / NULL watermark (MINOR → closed)

`last_folded_row_id IS NULL` means "never folded" — read from the
beginning, and the accumulator rollup handles the history. Same NULL
semantics as `consumed_verdict_tag` (migration 0037). No backfill.

### 6. Token budget at 200 rows (MINOR → implementation gate)

A 40-row fold prompt renders ~4.4k chars; 200 rows is ~5x (~22k chars)
plus prior and reasoning. `[standing] timeout_seconds = 20` may be tight.
Implementation must measure the actual 200-row prompt size and confirm the
timeout before shipping — a gate, not a design change.

## Operator simplifications

- **Accumulator threshold = fold batch = 200, one number.** Not two knobs.
- **Event fold enqueues through `ConversationSlots`** — it does not
  `try_acquire`-skip. A tag arriving mid-fold queues behind it (2-5s
  delay), never dropped.
- **Restart safety is a non-issue by construction.** The queue is
  in-memory scheduling; the watermark is Postgres state. A restart loses
  queued folds, re-derived from the watermark on the next message. No
  message and no backlog is lost. Recorded in 10-event-trigger §Restart
  safety.

## Decisions recorded

| # | Decision |
|---|---|
| D1 | Accumulator threshold = fold batch = `[standing] fold_batch = 200`, one number |
| D2 | Event fold serialized via existing `ConversationSlots` (enqueue, not skip) |
| D3 | Rollup folds do not increment `fold_count`; own counter `nora_standing_rollup_folds_total` |
| D4 | Verdict call reads the same batch as the standing call |
| D5 | NULL watermark = read from the beginning; no backfill |
| D6 | Config: `fold_batch = 200`, `event_enabled = true`, `rollup_max_folds = 5`; `window` key removed |
| D7 | Metrics: `nora_standing_event_folds_total`, `nora_standing_rollup_folds_total`, `nora_standing_watermark_gap` |
| D8 | Tag-path fold also switches to the watermark read; the 40-row window is gone entirely |

## Status / Checkpoint

_2026-09-01_ — review complete, decisions recorded. The bundle is ready
for implementation with the token-budget gate (finding 6) as the one
pre-ship check.
