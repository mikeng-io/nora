---
type: design
title: "Experiment and handoff — prerequisites landed, first replay blind, parked"
description: "Exact implementation state, source/eval defects, verification, bounded three-arm replay and the context-transfer procedure."
tags: [nora, context-plan, handoff, evals, implementation, verification]
timestamp: '2026-10-07'
edges:
  - {dst: 06-current-recommendation.md, rel: realizes, provenance: asserted}
  - {dst: 07-current-measurements.md, rel: constrained_by, provenance: asserted}
---

# Experiment and handoff

> **Reopened 2026-10-12 on a wider question** (node [09](09-what-a-turn-carries.md)):
> compose the whole per-turn prompt, doctrine included. The park below still
> holds for what it measured, a model selector over four optional blocks.

## 2026-10-07 result: the first replay was blind, and the bundle is parked

The prerequisites landed as PR [732](https://github.com/mikeng-io/nora/pull/732)
(merge `1320cf03`, gate 14,944 passed, issue 540 closed). The three-arm replay
below then ran on norty at that ref: `tool_selection`, one draw each of the
three cases, source not refreshed. Evidence is in [replay/](evidence/replay/):
per-arm result lines and receipts, with no room text.

| arm | pass | Nora in (fresh / cached) | selector |
|---|---|---|---|
| full | 2/3 | 113,315 / 192,244 | none |
| rules | 3/3 | 115,695 / 255,422 | none |
| flash | 2/3 | 93,883 / 232,312 | 0.74–1.03 s, 1,830–2,034 in / 13–16 out, provider cache header MISS (two reported ~290 cached prompt tokens), 0 errors |

**No arm withheld anything in any draw**, so Nora's exposed prefill was
identical in every arm (`exposed_chars == full_chars`). In the sandbox,
`peer_history` is empty (each draw gets a fresh Honcho workspace), `computer`
is empty (no VM state in the clone) and no subagent job is seeded. `documents`
rendered only in the document case, and Flash kept it, the one consequential
choice. The `route_computer_run_parse_csv` FAIL in full and flash is draw noise
over identical inputs. These arms were not shown to differ on a populated block
before the run; that check would have caught it without a model call.

Live, the selectable blocks are populated. From Langfuse `agent.prefill`, 427 tag
turns 2026-09-17 to 10-06: `peer_history` rendered in 384, `documents` in 28,
`computer` in 43 and `subagents` in 0. Since 09-30, `documents` was in 28 of 70
turns and `computer` in 43 of 70. All rendered blocks together were a median of
13,978 characters (p90 22,781). Per-block sizes are not recorded. Counts are in
[live-langfuse-counts.txt](evidence/replay/live-langfuse-counts.txt).

Reading it: the selector costs about 2k fresh input tokens and one serial
second per turn. The blocks it may withhold are a small part of a roughly 19k-
token call, and they are fresh about once per turn under the prefix cache. A
token or latency win is therefore unlikely. Any benefit would be reply quality
(less distraction), and that is unmeasured. Measuring it needs cases with
seeded `peer_history` and `computer` state, then the same three arms.

**Parked by the operator, 2026-10-07.** The eval-only filter stays in the tree,
inert unless `--context-arm` is passed. Reopen only with the seeded-case replay,
or with a measured quality problem that the optional blocks cause. The sections
below are the plan as it stood before the run.

## What is implemented

The current isolated worktree is `/Users/miles/Workplace/nora-context-router-replay`,
branch `mikeng-io/context-router-replay`, cut from `origin/master` at `1a20c236`.
The historical bundle was imported from design branch `feat/context-plan-design`
(PR [732](https://github.com/mikeng-io/nora/pull/732)). This local continuation has
not been pushed into that PR. Do not assume its remote head includes this work.

The user authorized two prerequisites:

* **Sender-name eval defect**, issue [540](https://github.com/mikeng-io/nora/issues/540).
  `know()` used synthetic account IDs even for known humans, but production name
  projection resolves `(platform, sender_id)`, not `person_id`. Known human seeds
  now use an account from the subject's loaded roster. Historical former person IDs
  resolve through `subject_id_for` for that account lookup while the original
  `person_id` remains stored. Unknown speakers, missing platform accounts and Nora
  rows keep synthetic IDs, avoiding collisions with roster accounts. No production
  account ID is copied into a fixture. The fix applies centrally to snapshot seeds.
* **Experiment-only filter**, `nora/subject/context_filter.py`, entered from the
  existing `python -m evals` CLI. No live pipeline/config changes. `--context-arm`
  chooses `full` (default), `rules`, or `flash`; `--context-report PATH` records
  receipts. Default full mode makes no selector call or patch. Explicit experiments
  temporarily patch the existing prefill assembly/Room seams, with per-call
  ContextVars isolating concurrent workers, then restore them.

`rules` omits the four optional blocks only for explicit arithmetic/simple
split-bill questions and retains full context for ambiguous requests. `flash` uses
Ollama Cloud DeepSeek V4.1 Flash through OmniRoute, a 10-second HTTP timeout,
temperature 0, reasoning effort none and output cap 128 tokens. It accepts only
optional names and the unchanged actual full transcript depth. The system prompt
and `_BLOCKS` routing substrate are in the same module. There is no second reply
agent, confidence cascade, selective retrieval or production router.

Culture, Standing, notes, identity and transcript references remain visible.
Withheld peer history clears the actual Room's `about` short-circuit flag; full
fallback restores it when that block exists. `TagContext` is not mutated.

Receipts contain draw name, tag ID, available/selected/withheld blocks, selector
latency, volatile-block character sizes, provider/cache/model identity, selector
usage and complete replay settlement time. They contain no room text. Existing
prefill `blocks_rendered` describes assembled material; the additional withheld
annotation describes the experiment's exposure restriction. The CLI fails an
experiment on selector error or on any draw lacking a measured prefill, even if
Nora's fallback reply passes.

## Verification state

Before this documentation consolidation, the final code tree passed `make gate`:
**14,944 passed, 14 skipped, 3 xfailed**, all three static checks passed. Snapshot
integration checks passed (30), including the real seeding path. Focused filter,
receipt-coverage and name-projection tests pass. The full-bundle final check and
committed-head readback are recorded in [verification](evidence/verification.txt);
use the recorded command/exit code, not an assumed green status.

Independent additive-code review found and verified fixes for malformed response
fallback, missing-draw evidence, former-person lookup and stale docstrings. The
peer-history amendment was independently verified with actual Rooms and an
interleaved two-task probe. No remaining consequential finding was reported.
Architecture registration subjects the new builder to the existing stricter
no-handwritten-tags check; it adds no exception.

Regression targets:

* `tests/unit/test_subject_knowledge.py::test_know_seeds_roster_senders_for_production_name_projection`
* `tests/test_prefill.py::test_eval_context_filter_withholds_only_selected_optional_blocks`
* `tests/test_prefill.py::test_eval_context_filter_invalid_plan_preserves_full_context`
* `tests/unit/test_eval_main_shared_db_guard.py::test_context_experiment_requires_a_prefill_receipt_for_each_draw`

Mutation verification must be read back from its log. A green test alone does not
prove the defect is guarded. Run existing mutation tables naming touched files:
`eval-note-date-split`, `instagram-shared-reel-cover`, `note-format-s1a-roster-names`.
The checkpoint's results are saved with the verification receipt. The next landing
session must update against current master: the local branch began at `1a20c236`,
while `origin/master` advanced to `67520d7a` during this session. This checkpoint
does not incorporate that later conversational-recall design bundle.

## Replay had not run (before 2026-10-07; superseded by the result above)

No downstream Nora accuracy, reply-quality or net-latency benefit is established.
All reported model accuracy numbers are the synthetic probes in node 07.
Issue [462](https://github.com/mikeng-io/nora/issues/462) is closed (draw memory
isolation). Issue [502](https://github.com/mikeng-io/nora/issues/502) remains open:
the existing judge times out or returns provider errors on long prompts. The first
comparison uses the structural tool-routing grader; it does not route around that
issue and does not claim reply quality.

Prepared host eval worktrees, currently at the pre-change base:

* `~/workspace/nora-wt-context-replay-full`
* `~/workspace/nora-wt-context-replay-rules`
* `~/workspace/nora-wt-context-replay-flash`

Each already ran `uv sync --extra evals`. Advance each to the exact reviewed,
landed prerequisite ref before replay; development still happens on the MacBook.
Live checkout `~/workspace/nora` and its ignored config were untouched. Existing
untracked backups/scratch files on either machine belong to other work.

## Smallest viable next measurement (run 2026-10-07; result at the top)

Select these anchored `tool_selection` cases, one draw each, on the same source
and deployment main-model alias:

1. `route_calculate` — self-contained Cantonese split-bill control.
2. `route_document_revise_id_only_in_the_block` — document ID exists only in the
   seeded context, so omission has a concrete downstream consequence.
3. `route_computer_run_parse_csv` — computation/tool-routing control.

Three cases × one draw × three arms × the historical 211-second draw estimate is
about **32 minutes**. It is a smoke comparison, not an accuracy-rate estimate.
The cases do not establish personal-recall or subagent-selection quality.

Follow `nora-land` to reconcile the existing design PR and land the prerequisite
fix, then `nora-eval` for exact refs, lock, source checks and sequential arms.
The repo requires a declared-broken eval's fix to land before running it; do not
create another harness. Do not refresh the shared source between arms. Hold
`/tmp/nora-eval.lock` for all arms and check no eval/clone refresh is already live.
Use the live deployment's config and environment without printing secrets.

Example command in EACH exact-ref host worktree:

```bash
uv run python -m evals tool_selection --sample 1 --reasons \
  --case route_calculate \
  --case route_document_revise_id_only_in_the_block \
  --case route_computer_run_parse_csv \
  --context-arm full --context-report /tmp/context-full.json
```

Substitute the arm/report path for `rules` and `flash`. Capture stdout, exit code,
code SHA, source identity and receipts. Before paying for replay, prove the arms
reach the composed prefill and differ on a concrete rendered input. Record
actual cache hits/misses; a cached response is not fresh inference latency.

Read FAIL/ERROR as findings, not reasons to redraw until green. Inspect model
input, source labels and real tool-return shapes offline. Report main-model cached
and fresh usage separately from selector usage. Settled replay latency is not time
to the first delivered reply. Do not promise 1-second Nora replies from a 1-second
selector microbenchmark.

## Open questions and boundaries

* Does exposure selection improve cognition rather than only shortening text?
* Can it preserve required-context recall on ambiguous/mixed Cantonese-English
  inputs and long conversations? The eight synthetic scenarios do not answer it.
* Is net main-model latency/cache cost better after including selector cost?
* How should the reply-quality judge defect be fixed before a quality claim?
* Is a zero-call deterministic selector adequate at this volume?
* Do Tev1/another small LLM or a pre-ranker improve the same labelled metric?
* What happens under shared ASR/TTS/gate contention on norty? No GPU purchase is
  decided; A2000/A3000 LP results remain unmeasured.

No production contract, transcript reduction, selective retrieval, deployment or
GPU installation is authorized by this checkpoint. Reopen those choices only on
new measurements. The next session must first read the current source and this
bundle; previous chat architecture is not an authority.

## 2026-10-07 live replay result, and what shipped instead

Replay on norty (`~/replay-router`, recorded tag turns against the deployed
alias), two selector arms over the 216 turns:

| arm | withheld something | first-call input | notes |
|---|---|---|---|
| default-include selector | 11 / 216 | about unchanged | nearly nothing cut |
| inverted (default-exclude) | 189 / 216 | 18.5k to 15.6k tokens (-16%) | costs about 2.6k selector tokens and 1.7 s serially |

The inverted arm cuts first-call input, but the selector call is paid in series
and later calls in the turn hit the prefix cache either way, so the net is a
wash. **A model router does not pay at this volume; a rule-based cut does.**
Two rule-based cuts follow from it and ship without a selector: the WHO speaker
set of `<character>` (#753) and this node's transcript depth.

**Shipped: `[store] prompt_window = 20`** (default in code). The tag prompt's
`<group_transcript>` shows the newest 20 rows of the 40 read, reaching further
back only to include a quoted parent. `context_window` stays the read depth, so
`who_said` still searches 40 and DEC-195's quote growth (cap
`context_window * quote_window_multiplier`) is unchanged. Measured the same day
on norty: median `<group_transcript>` 5,331 characters at 40 rows against 2,513
at the newest 20 (119 recent `engagement` traces, all full at 40; about 2,800
characters, 52% of the block, of a ~63k-character first call); 11 of 622
recorded replies in 30 days (1.8%) quote a row more than 20 back, 5 more than 40.
