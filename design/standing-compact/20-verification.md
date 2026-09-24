---
type: contract
title: "Verification — the fold measured on real moments, both arms"
description: "The standing corpus, what each case grades, the unit tests that pin the render, and the live readings that would reopen a decision."
tags: [nora, standing, debrief, verification, eval]
timestamp: 2026-09-24
edges:
  - {dst: 10-changes, rel: depends_on, provenance: asserted}
---

# Verification

## 1. A standing corpus (`evals/corpora/standing.yaml`)

Until now nothing measured a fold. The debrief ran inside every eval turn and
nobody graded what it wrote. This bundle changes what the fold is shown and
told, and **a prompt change is not judged on one example**
([AGENTS.md](../../AGENTS.md), and the Relevance Gate's 3→5→15 regression). So
it ships with a corpus.

**Every case is a real moment**, per the suite's one-path rule. A case names a
tag turn (`tag_id`) in the `nora_eval` clone. The harness reads that turn's
`tag_trace`, the watermark batch the fold would have read, and **the position
as it stood before the turn** (`SqlStandingStore.read_as_of`, the chain from
migration 0059). It then runs the real fold stage against the live model.
Nothing about the room is authored into the tree.

**Graded by the judge against a per-case property.** The output is Cantonese
prose, and the properties are semantic. Each case states ONE property of the
written position:

| kind | the property | the claim it tests (00 §The claim) |
|---|---|---|
| `carry` | a thread the prior held, which this turn did not touch, is still held | 1: no path blanks the prior |
| `stale` | a clause whose relative time (today, tomorrow) the turn's date has passed is dropped, or rewritten with its date, and not re-affirmed as current | 2: the clock |
| `residue` | a finding from a tool result that the reply did not send is carried, with when it was found | 3: the steps at full size |
| `recap` | the position is not a recap of the transcript (control: unchanged by this bundle, must not regress) | the existing shape |

`unavailable` (timeout, prose instead of JSON, the fold refused) is ERROR, never
PASS.

**Both arms, and the arms are proven to differ first.** The master arm is
master's fold (600-character clip, no `written`, `<reasoning>`, the old
instruction). The branch arm is this bundle. Before any model call, each arm's
rendered input for one case is printed and diffed. A suite that cannot see the
change measures noise.

**Samples, not a single run.** A model judging prose varies run to run: the
Relevance Gate's winning build scored 0 to 2 errors on the same 40 cases. Each
case runs at `--sample 3` at least, and a result is reported as a count over
samples, never as a rate from one draw.

## 2. Unit tests that pin the render

- `<standing_prior>` carries `written` as an age against the turn's time, on
  the tag path and on the rollup.
- `<steps>` replaces `<reasoning>`. A `reasoningContent` block renders with
  `kind="thinking"`. A turn with none renders no `kind` attribute anywhere.
- A tool result of 4000 characters renders whole. One of 4001 is clipped,
  marked, and counted on `nora_debrief_clips_total{block="tool_result"}`. Model
  narration and tool calls still clip at 600.
- The fold's prompt contains the prior on EVERY turn, including one after 20+
  folds. Nothing renders "no position yet" when a prior exists. This pins the
  deletion of both triggers: reverting either reddens it.
- One model call per tag fold and per rollup fold, not two.

## 3. Live readings after deploy

| reading | healthy | reopens |
|---|---|---|
| debrief model calls per fold, in Langfuse | 1 (2 on a consolidate retry) | anything else |
| `nora_debrief_seconds` p95 | ≤ 5s ([26 §5](../dreamer-agent-loop/26-standing.md)) | above it: the larger tool results cost the slot; lower the bound |
| `nora_standing_overflow_refusals_total` | occasional | constant: the working set crowds the cap; revisit `max_chars` or what belongs |
| stale clauses in `standing` | none older than a few days re-affirmed as current | the ratchet §10-changes §1 names: reconsider a bound |
| `nora_debrief_clips_total{block="tool_result"}` | falls sharply from today's rate | unchanged: the bound is not where the results are |
| `nora_dream_screened_total{adaptor="debrief"}` and `nora_debrief_total{outcome="screened"}` | rare | rising: findings are carrying identifiers and whole folds are being dropped (the prior stands, but nothing new lands) |
| tag-turn replies that act on a finding in `<standing>` as though it were a commitment or a fact she established | never | any: the finding/commitment line in the objective is not holding (security review, 10-changes §5) |

## 4. Running the master arm

Master has no standing entry point. The master arm is master's production code
with the harness applied on top (`evals/`, `nora/subject/standing_fold.py`,
applied cleanly as of 2026-09-24), run from its own checkout; the corpus header
says how. The sandbox seeds `turn_verdict` as of each case's instant, so on
master the verdict-triggered re-derivation happens where it happened in
production.

## 5. Measured, 2026-09-24

Both arms on master `f5a1bb97` (the master arm has the harness applied),
`--sample 3`, 13 anchored cases. Arms proven to differ first: the master
input renders twice (fold + verdict), `<reasoning>`, `at="04:06"` and no
`written`; the branch input renders once, `<steps>`,
`at="2026-09-06 04:06"` and `written=`.

| kind | master | branch |
|---|---|---|
| carry (4) | 6/12, 1 error (judge 504) | **12/12** |
| stale (4) | 1/12 | **8/12** |
| recap control (2) | 6/6 | 6/6 |
| residue (3) | 0/9 | **0/9** |

**Claims 1 and 2 hold on this corpus. Claim 3 does not.** Master failed
carry exactly where production re-derived: the two rooms whose priors were
blanked by the verdict and by the ceiling. On the branch, the fold keeps
them. Stale clauses are re-affirmed on 11 of 12 master draws and 4 of 12
branch draws.

Residue is 0/9 on both arms, although `--print-input` shows the tool results
reaching the fold whole. Two of the three residue cases ask it to carry page
detail nobody in the room could use, which the objective's own «still useful
to someone here» excludes. The third, a valid case, fails on both arms. One
added objective sentence («a finding passes the recap test by construction»)
was measured on a second branch run and moved nothing (residue 0/9, stale
9/12), so it was reverted. Tracked in #445: read the written positions
before touching the instruction again. Is the 1200-character cap evicting
the finding, or does the working set need its own field?

## Status / Checkpoint

2026-09-24: written, built and measured (§5). Claims 1 and 2 hold; claim 3
is open in #445.
