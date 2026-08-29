---
type: contract
title: "Verification — how we know the split worked"
description: "Unit tests for call independence and latency shape; the live falsifier that decides the bundle's claim."
tags: [nora, standing, debrief, verification, falsifier]
timestamp: 2026-08-29
edges:
  - {dst: 10-split-call, rel: depends_on, provenance: asserted}
  - {dst: 20-ratchet-and-tiebreak, rel: depends_on, provenance: asserted}
---

# Verification

## Unit

- **Call independence** — standing output and verdict output come from
  separate completions; a change to the standing prompt does not change the
  verdict prompt (assert the two prompts differ in the right ways: verdict
  includes outcome, standing includes prior).
- **Verdict sees the whole turn** — the verdict render includes `<outcome>`;
  the standing render does not.
- **Latency is max not sum** — both calls start before either finishes (mock
  timing or assert `asyncio.gather` usage); the fold's wall time is bounded
  by the slower call, not the sum.
- **Ratchet** — the objective says re-judge, not keep-its-wording; the
  re-voice rule is present.
- **Tiebreak** — synthesis wins over room-said in the consolidation step.

## Live falsifier (the one that matters)

After deploy:

1. **Standing rows** — first-person position shape ("我 commit 咗…", "我答
   咗…") instead of third-person event lists ("Nora 答咗…", "Tyler 食緊飯").
2. **`turn_verdict` grounded ratio** — if it stays 21/21 → 30/30 after the
   split, the judge prompt itself is the problem, not the call structure.

## Rollback

Revert the merge. Phase 1 stays — it is strictly better than pre-Phase-1,
even if not sufficient.

## Status / Checkpoint

_2026-08-29_ — proposed. Falsifier declared in 00-intent; this node is the
checklist that runs it.
