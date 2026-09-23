---
type: design
title: "Intent — why the fold splits"
description: "The claim this bundle makes, the evidence it rests on, and the falsifier that decides whether it worked."
tags: [nora, standing, debrief, split-fold, issue-78]
timestamp: 2026-08-29
edges:
  - {dst: 10-split-call, rel: sequences, provenance: asserted}
  - {dst: 20-ratchet-and-tiebreak, rel: sequences, provenance: asserted}
  - {dst: 30-verification, rel: sequences, provenance: asserted}
---

> **SUPERSEDED IN PART 2026-09-24 by [design/standing-compact/](../standing-compact/00-intent.md).** The falsifier below fired: `turn_verdict` read 380 true / 25 false after the split, and 17 of the 25 came from a turn whose tool results the judge saw cut to 600 characters. Call B is deleted; the position call (A) and the ratchet/tiebreak rules stand.

# Intent

## The claim

The debrief fold's two outputs — standing revision and groundedness verdict —
are two different jobs that share one model completion. The sharing is the
root cause of both live symptoms:

1. **Third-person recap rows.** The observer cast ("You are not the assistant
   who answered it") made the fold a non-participant with no position to
   revise, so it wrote transcript summaries. Phase 1 fixed the cast; the
   falsifier fired anyway — the first post-deploy fold still wrote an event
   list. The cast was necessary but not sufficient: the ratchet
   (keep-its-wording) and the tiebreak (room-said outranks synthesis) still
   preserve and prefer recap-shaped text.

2. **21/21 grounded=true.** The verdict has never discriminated. A model asked
   to write a position and then judge its own sourcing in the same completion
   does not convict itself. This is structural, not a prompt defect.

## Evidence

- `nora/engagement/stages/debrief.py:939-973` — `_fold()` is one completion,
  one prompt, two outputs.
- `nora/dreamer/objectives/standing-debrief.md` v2 — first-person cast live in
  container (verified 2026-08-29 13:49 UTC).
- Live DB — standing row written 13:49:08 UTC (5 min after container start)
  is a third-person event list; `turn_verdict` 21/21 grounded=true.
- `design/dreamer-agent-loop/26-standing.md` §1 — "It is a SECOND model
  reading a FINISHED exchange. Not the model that replied, reflecting — a
  different call, seeing the whole turn including its own outcome. The
  argument is **impartiality, not cost**."

## The falsifier

Post-deploy, watch:

- **Standing rows** — first-person position shape ("我 commit 咗…", "我答
  咗…") instead of third-person event lists ("Nora 答咗…", "Tyler 食緊飯").
- **`turn_verdict` grounded ratio** — if it stays 21/21 → 30/30 after the
  split, the judge prompt itself is the problem, not the call structure.

If rows remain event lists and the ratio stays 100%, this bundle's claim is
wrong and the levers are the judge prompt and the position shape, not the
call split.

## Status / Checkpoint

_2026-08-29_ — bundle drafted, proposed. Phase 1 shipped at fe3301bd; this
bundle is the Phase 2 structural fix.
