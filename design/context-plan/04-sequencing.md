---
type: design
title: "Sequencing — instrument first, selector only against a measurement"
description: "Four steps, each with an acceptance criterion and a reversal condition. Step 1 is inert and lands on the existing suite; steps 2 to 4 are gated, and step 4 (selective retrieval) is not justified by anything measured so far."
tags: [nora, context-plan, sequencing, acceptance, evals, reversal]
timestamp: '2026-10-05'
edges:
  - {dst: 00-overview.md, rel: realizes, provenance: asserted}
  - {dst: 03-measurement-decision-models.md, rel: constrained_by, provenance: asserted}
---

# Sequencing

```text
  S1  PLAN-TYPE + FILTER — the value, the filter, the annotation, plan=None
      │   inert: no production caller passes one
      ▼
  S2  MEASURE WHAT IS ALREADY THERE — per-block usefulness, from recorded turns
      │   no model, no new dependency, the answer the operator actually wants
      ▼
  S3  ONE BOOLEAN SELECTOR (optional) — a single noul question, fail-open
      │   only if S2 shows a block that is both large and frequently useless
      ▼
  S4  SELECTIVE RETRIEVAL (deferred) — the plan moves before the gather
          gated on a latency measurement that does not exist and cannot be
          inferred from S3
```

## S1 — the value, the filter, the annotation (inert)

Add `ContextPlan` to `nora/domain/blocks.py`; add a `plan: ContextPlan | None`
parameter to `build_prefill`; skip blocks not in the plan while still walking the
table, so `blocks_declared`/`blocks_empty`/`blocks_rendered` keep their meaning.
Add `nora.prefill.plan` to the prefill span (the names withheld and the reason).

**Acceptance.**

1. `plan=None` renders **byte-identical** output to today, pinned by a golden
   over the existing fixtures (`tests/test_prefill.py` already holds the framing
   and ordering assertions; `tests/unit/test_*_goldens.py` shows the shape).
2. A plan naming a block that `_BLOCKS` does not declare is a construction
   error, not a silently-empty block.
3. `test_the_served_block_set_has_not_quietly_emptied` still passes — a plan must
   not be able to empty the served set on a normal fixture.
4. The architecture test
   (`tests/architecture/test_every_model_call_is_a_declared_occasion.py`) stays
   green: the filter must not be written as a hand join.
5. `TagContext` is untouched: the diff shows no new field and no
   `dataclasses.replace` that nulls one.

**Reversal condition:** if (1) is not byte-identical, stop — that is a prompt
change, not an instrument, and it wants the operator's eval before anything else.

## S2 — measure what is already there (the recommended step, and it needs no model)

This is the part of the idea worth building, and it is a **measurement over
recorded turns**, not a new component. Nora already records, per turn:

* `nora.prefill.blocks_declared` / `blocks_rendered` / `blocks_empty` /
  `size_chars` on the `agent.prefill` span (`nora/agent/prefill.py:1077-1084`);
* the full rendered input of every generation in Langfuse (the trace store
  already holds `input` for each GENERATION);
* `TagTrace` per turn (`nora/domain/tag_trace.py`), the `tool_selection` and
  participation suites, and the existing eval harness (`evals/`).

So the question "which blocks does this turn not need?" can be answered for a
hundred recorded turns before a single model is put in front of anything, using
a judge the repo already has (`[llm.deep]`, chosen precisely because same-model
self-eval is a tautology — `devops/nora`, "Eval judge").

**Deliverable:** a per-block table over recorded turns — presence, rendered size,
and a graded usefulness judgement — plus the correlation the design actually
needs: *does a turn that did not need `<culture>` differ from one that did, in a
way a cheap signal can see?* If no cheap signal separates them, the selector
cannot work and S3 is closed by evidence rather than by argument.

**Acceptance:** the table exists, the grading is a named eval arm, and it is run
on the same corpus the participation gate uses so the numbers are comparable. No
new production code.

**Why this instead of S3:** it is the cheapest way to test the premise, and the
premise is the thing in doubt. `design/proposed/18`'s fate is the precedent to
respect — ADR 0003 deferred `ContextBudget` because its problem was **unmeasured
while its counter already existed**; building a component to manage a pressure
nobody has read is building against a guess.

## S3 — one boolean, fail-open (optional, gated on S2)

If S2 names a block that is both large and frequently useless, the honest
selector is **not a plan**. It is one `noul` question per candidate
(~185 input tokens, sub-second warm on the 0.8B model, §3 of `03`), asked before
the main call, defaulting to **show everything**:

```text
plan = None                          (default: full context, today's behaviour)
if selector answered with confidence >= threshold:
    plan = full_minus(block)         (the only power the selector has)
```

**Failure behaviour is the design, not an afterthought** (the operator's Q7):

| failure | behaviour | recorded as |
|---|---|---|
| endpoint unreachable / times out | full context | `nora.plan_selector_total{outcome="unavailable"}` |
| answer below the confidence threshold | full context | `…{outcome="low_confidence"}` |
| answer not parseable | full context | `…{outcome="malformed"}` |
| the selector would drop a **mandatory** block (§2 of `02`) | full context, and count it | `…{outcome="refused_mandatory"}` |
| a plan that would empty the served set below the non-degeneracy floor | full context | `…{outcome="refused_degenerate"}` |

This is the repo's three-state discipline (`ok` / `nothing_found` /
`unavailable`, R-27) applied to a selector: an ERROR is never a PASS, and
"nothing withheld" is a legitimate answer rather than a failure. It also has to
be a **timeout-bounded** call inside the turn's deadline
(`nora.domain.turn_deadline`), and it must not consume the reply budget
(`ReplyGuard`, R-7) — it is not a tool.

**Acceptance:** the eval suite (`evals.run`) shows no regression on the tag and
participation corpora; the selector's arm is graded against S2's table, not
against its own agreement with itself.

**Reversal condition:** if the selector's per-turn grade is no better than
"always show everything" on the corpus, it is deleted, and the bundle records
that it was measured and closed.

## S4 — selective retrieval (deferred, and not justified)

The operator's Phase 2. It is a real design (a `RoutingContextAssembler`, then
the plan, then a `PayloadContextAssembler`) and `03` does not support it:

* it serialises a decision in front of the only concurrent gather the tag path
  has (`nora/engagement/context.py:239-254`);
* the reads it would skip are 100–270 ms p95 each, except one at 1,860 ms p95;
* the decision that would skip them costs 6–33 s locally today.

The condition under which S4 becomes worth doing, stated so it can be checked
rather than argued: **a selector whose total added latency is under ~150 ms and
whose substrate is already assembled** — i.e. a hosted decision call, or a local
model on a working iGPU with a measured sub-150 ms plan. Neither exists here
today.

**Also deferred with it:** plan-driven row filtering (sender, recency, threading,
`02` §3 item 3) — that requires the plan to run *before* `name_transcript` and
the media join, which is S4's shape and S4's cost.

## What none of these steps do

They do not touch doctrine (72% of the prompt), the tool surface, or the skills
list. If the goal is cost, that is where the number is, and it is a prompt-authoring
question (`design/17-prompt-substrate.md`), not a context-selection one.
