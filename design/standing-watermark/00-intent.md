---
type: design
title: "Intent — why the window is the wrong read"
description: "The claim this bundle makes, the evidence it rests on, and the falsifier that decides whether it worked."
tags: [nora, standing, watermark, event-driven, rollup]
timestamp: 2026-09-01
edges:
  - {dst: 10-event-trigger, rel: sequences, provenance: asserted}
  - {dst: 20-watermark-read, rel: sequences, provenance: asserted}
  - {dst: 30-rollup, rel: sequences, provenance: asserted}
  - {dst: 40-verification, rel: sequences, provenance: asserted}
---

# Intent

## The claim

Standing's current read — the last 40 rows of the conversation, the same
window the reply path uses — is the wrong read for a position that must
inherit context across untagged stretches. The window clips: a conversation
that passes 40 messages between tags loses everything before the window,
and the position cannot inherit what it never saw.

The fix is a **row-id watermark**: every fold reads `since(last_folded_row_id)`
— everything that arrived since the last fold, capped at 200 — and the fold
is triggered **event-driven** from the ingest path, not by a background loop.

## Evidence

- `nora/engagement/stages/debrief.py:503-508` — `window` is capped at
  `[store] context_window` (40) and equals it: the fold reads `ctx.transcript`,
  the rows the reply path already assembled.
- `nora/infra/sql/audit.py:109-145` — `recent()` is newest-first, `limit` rows.
  A conversation with 60 messages and no tag in between: the fold sees rows
  21-60, rows 1-20 are gone from the read.
- Live DB 2026-08-31 — Discord #discussion fold 7's transcript spans 7 days
  (2026-08-24 → 08-31) at 40 rows; the conversation had more history than the
  window could carry.
- `design/dreamer-agent-loop/26-standing.md` §4 — the original rejection of a
  watermark slice named two reasons: `since` excludes Nora's own rows in SQL,
  and `tag_id` has no ordering relation to message ids. The first is fixable
  with a new read; the second is moot — the watermark is a row id, not a
  tag id.

## The falsifier

Post-deploy, watch:

- **Standing rows after an untagged stretch** — a conversation that passes
  40 messages between tags must produce a position that reflects the WHOLE
  stretch, not just the last 40. If the position still reads like it only
  saw the tail, the watermark read is not actually reaching the older rows.
- **Rollup convergence** — a cold-start conversation (no prior fold) must
  converge to a position that covers its full history within
  `ceil(history / 200)` folds. If it stalls at one fold, the rollup trigger
  is not re-firing.

If rows still clip at 40 and cold starts still see one fold, this bundle's
claim is wrong and the levers are the trigger wiring and the read, not the
window size.

## Status / Checkpoint

_2026-09-01_ — bundle drafted, proposed. Operator decisions recorded in the
index: event-driven trigger (no background loop), cap 200, rollup cold start,
new read including Nora's rows, watermark column on the standing row.
