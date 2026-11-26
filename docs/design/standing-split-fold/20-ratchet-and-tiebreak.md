---
type: design
title: "Ratchet and tiebreak — stop recap-shaped text surviving"
description: "Re-judge the whole paragraph instead of revise-in-place; synthesis outranks room-said in the position."
tags: [nora, standing, debrief, ratchet, tiebreak]
timestamp: 2026-08-29
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
  - {dst: 10-split-call, rel: depends_on, provenance: asserted}
---

# Ratchet and tiebreak

## The ratchet — re-judge, don't revise-in-place

`nora/dreamer/objectives/standing-debrief.md:21` currently says "keep its
wording and change only what this turn changed." This preserves inherited
third-person clauses — the live prior is a third-person recap, so the first
several post-deploy folds produce mixed-person paragraphs ("我已答" next to
"Nora 問佢"). Phase 1 added a re-voice rule as prose; prose does not move
behaviour (DEC-78/DEC-92 finding).

**Change:** the fold re-judges the whole paragraph each turn. Inherited
clauses are kept only if they still hold AND are re-voiced in the fold's own
voice. The split call gives the standing call room to rewrite rather than
patch — a full rewrite is cheaper when the call is dedicated to it.

## The tiebreak — synthesis outranks room-said

`nora/engagement/stages/debrief.py:383-392` — the consolidation step lets
room-said outrank synthesis. The position should prefer what Nora concludes
(synthesis) over what people said (room-said). Room-said belongs in the
transcript, not the position. Re-tie so synthesis wins when both are present.

## Why both matter for the falsifier

The cast fix (Phase 1) alone did not change the row shape because the ratchet
preserved the old recap text and the tiebreak preferred it. Both mechanisms
must move for the falsifier to pass.

## Status / Checkpoint

_2026-08-29_ — proposed. Cost note: full re-judge costs more tokens per fold;
benefit is no inherited third-person text survives.
