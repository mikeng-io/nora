---
type: design
title: "Rollup — cold start and backlog in batches"
description: "A conversation with no prior fold, or more new rows than the cap, folds repeatedly — each fold's position carrying the previous one forward — until caught up."
tags: [nora, standing, rollup, cold-start]
timestamp: 2026-09-01
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
  - {dst: 20-watermark-read, rel: depends_on, provenance: asserted}
---

# Rollup

## The shape

The fold is a **batch operation**: it reads the next `fold_batch` (200) rows
since the watermark, produces a position, advances the watermark. If more
rows remain, the next trigger folds again. The position is the compression
that carries the earlier context forward — that is what makes the batches
composable.

```
Conversation with 500 rows, never folded (watermark NULL):

Fold 1:  prior(无) + rows 1-200   →  position A, watermark = 200
Fold 2:  prior(A)  + rows 201-400 →  position B, watermark = 400
Fold 3:  prior(B)  + rows 401-500 →  position C, watermark = 500  (caught up)
```

Each fold sees 200 rows PLUS the previous position — the position is the
memory of everything before it. This is the operator's "roll up": run the
standing fold in batches, each batch's position inheriting the last.

## Why this works

- **The position is lossy by design** (`26-standing.md` §8) — it is exactly
  the compression a rollup needs. A position that kept everything would make
  the batches redundant; a position that keeps nothing would make them
  useless. The tier's whole shape is the rollup's mechanism.
- **The ratchet re-judges the whole paragraph** (split-fold 20) — each fold
  walks the inherited position and keeps only what still holds. A rollup
  fold is the same re-judgement, just with a larger delta to absorb.
- **The cap bounds each call, not the conversation.** 200 rows per fold is
  a prompt budget; the conversation can be any size.

## Cold start

`last_folded_row_id IS NULL` is the cold-start case. The first fold reads
the first 200 rows of the conversation (from the beginning, not the end —
`since_including_nora(after_id=0)`), and the rollup proceeds from there.

A conversation that has been running for weeks with no fold will take
`ceil(history / 200)` folds to catch up. Each fold is a model call, so a
very long history costs several calls — accepted: the alternative is a
single fold that either clips (the current window) or blows the prompt
budget.

## Triggering the rollup

The event trigger (10) fires on every message. A conversation with a large
backlog folds once per message until caught up — each message's check sees
rows still remaining and folds again. This is correct but can be slow: a
500-row backlog folds 3 times across 3 messages, not 3 times in one burst.

**Option: fold-until-caught-up in one spawn.** The check, once spawned,
loops: fold, advance watermark, check for more rows, fold again — bounded
by a max folds per spawn (e.g. 5) to avoid a single message triggering an
unbounded cascade. This catches a backlog up in one event instead of
stretching it across several.

**Recommendation: fold-until-caught-up, bounded.** The operator's "roll up"
reads as one operation, not a drip. Bound it (max 5 folds per spawn) so a
pathological conversation cannot monopolise the model budget, and let the
next message's check continue if the bound is hit.

## Failure modes

- **A rollup fold fails mid-way.** The watermark does not advance (atomic
  write), so the next check retries the same stretch. The position stays
  stale but consistent — never a position that claims to have read further
  than it did.
- **A conversation that never stops growing.** If new messages arrive faster
  than folds complete, the watermark never catches up. The bound on folds
  per spawn caps the damage; the position is always at least as fresh as the
  last successful fold, which is strictly better than the current window
  (which clips regardless of fold success).

## Status / Checkpoint

_2026-09-01_ — proposed. Operator decision: rollup in batches, each fold
carrying the previous position forward. Fold-until-caught-up bounded at 5
per spawn is the recommendation; the bound is a knob.
