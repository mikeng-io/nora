---
type: design
title: "Split-call — two parallel completions"
description: "The fold becomes two independent model calls via asyncio.gather; latency is max not sum; the verdict sees the whole turn."
tags: [nora, standing, debrief, split-fold, asyncio]
timestamp: 2026-08-29
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
  - {dst: 30-verification, rel: sequences, provenance: asserted}
---

# Split-call

## The shape

```
_fold() → asyncio.gather
  ├─ Call A: standing revision
  │    prompt: <standing_prior> + <transcript> + <reasoning>
  │    output: {standing: "..."}
  │    viewpoint: first person, Nora revising her own position
  │
  └─ Call B: verdict
       prompt: <transcript> + <reasoning> + <outcome>  (the whole turn)
       output: {grounded: true/false, unsupported: [...]}
       viewpoint: impartial judge, sees the whole turn including its own outcome
```

## Why parallel

Call B does not depend on Call A's output. They run concurrently.
latency = max(A, B), not A + B. Both prompts can be smaller than the current
combined one, which relieves the `config.toml` 45s tightening pressure (the
comment "once the image carrying the new validator is deployed" — the image
is deployed; this is the same decision).

## Why the verdict sees the whole turn

The current single prompt mixes the standing prior into the judge's view.
The judge should see what actually happened — transcript, reasoning, outcome —
not the position it is about to write. Removing the prior from Call B removes
the prompt pollution where first-person position-writing contaminates the
verdict.

## Model identity

Same model for both calls. The design says "a different call" — different
call, not necessarily different model. Impartiality is the call separation,
not model identity. (Cheaper; and the falsifier would catch a model-specific
bias if one existed.)

## Files

- `nora/engagement/stages/debrief.py` — `_fold()` split; `asyncio.gather`
- `nora/engagement/debrief_render.py` — verdict render gets the whole turn;
  standing render unchanged
- `nora/config.py` — StandingConfig: two calls (same model, or one config
  for both)
- `nora/dreamer/objectives/standing-debrief.md` — v3: re-judge posture;
  verdict half moves to its own objective (`standing-verdict.md`) or stays
  with a "you are judging, not writing" frame

## Status / Checkpoint

_2026-08-29_ — proposed. Decision points: separate verdict objective file
(recommended), same model both calls (recommended), split now (recommended).
