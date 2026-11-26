---
type: contract
title: "Verification — how we know the watermark works"
description: "Unit tests for the read, the trigger, and the rollup; the live falsifier that decides the bundle's claim."
tags: [nora, standing, watermark, verification, falsifier]
timestamp: 2026-09-01
edges:
  - {dst: 00-intent, rel: depends_on, provenance: asserted}
  - {dst: 10-event-trigger, rel: depends_on, provenance: asserted}
  - {dst: 20-watermark-read, rel: depends_on, provenance: asserted}
  - {dst: 30-rollup, rel: depends_on, provenance: asserted}
---

# Verification

## Unit

- **`since_including_nora` returns Nora's rows** — the new read includes
  `is_nora` rows, unlike `since`. Asserted against the contract suite with
  the real store, not a fake.
- **Watermark advances atomically** — a fold that writes the position also
  writes `last_folded_row_id` in the same statement; a fold that fails
  advances neither. Asserted by a test that fails the write and checks the
  watermark is unchanged.
- **Event trigger fires only on new rows** — a check with no rows since the
  watermark does not call the model. Asserted with a fake client that
  counts calls.
- **Rollup converges** — a conversation with 500 rows and no prior fold
  produces 3 folds (200 + 200 + 100), each with the previous position as
  prior, and the final watermark is 500. Asserted with a fake client that
  records prompts.
- **Rollup bound** — a conversation with 2000 new rows folds at most 5
  times per spawn; the next spawn continues.
- **Re-derivation does not advance the watermark** — a rebuild re-reads the
  same stretch and leaves `last_folded_row_id` unchanged.

## Live falsifier (the one that matters)

After deploy:

1. **Untagged stretch** — let a conversation pass 40 messages without a tag.
   The standing row must reflect the WHOLE stretch, not the last 40. If it
   still reads like the tail, the watermark read is not reaching the older
   rows.
2. **Rollup convergence** — a conversation that has never folded (or has a
   large backlog) must converge to a position covering its full history
   within `ceil(history / 200)` folds. If it stalls at one fold, the trigger
   is not re-firing.
3. **Freshness without tags** — a conversation that is active but never tags
   Nora must still have a standing row that updates as messages arrive. This
   is the property that did not exist before the bundle.

## Rollback

Revert the merge. The tag-path fold stays — it is strictly better than
pre-bundle, even if the untagged stretch is still clipped.

## Status / Checkpoint

_2026-09-01_ — proposed. Falsifier declared in 00-intent; this node is the
checklist that runs it.
