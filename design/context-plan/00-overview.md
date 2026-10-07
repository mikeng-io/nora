---
type: design
title: Historical October 5 — Overview — the idea, the two things the repo says about it, and the verdict
description: Historical October 5 finding, superseded in relevant part by nodes 06–08 on October 7.
  What the operator asked for; why the block table is already the right seam and no new pipeline layer
  is needed; why the token/latency half of the premise is contradicted by measurement; and the one part
  of the idea the repo actively agrees with.
tags:
- nora
- context
- decision-model
- challenge
- verdict
timestamp: '2026-10-05'
edges:
- dst: 01-the-seam.md
  rel: sequences
  provenance: asserted
- dst: 03-measurement-decision-models.md
  rel: informs
  provenance: asserted
---

> **PARTLY DEPRECATED (2026-10-07).** Historical 2026-10-05 proposal and CPU measurements. The current recommendation, acceleration measurements, operator authorization and implementation state are in [06](06-current-recommendation.md), [07](07-current-measurements.md) and [08](08-experiment-and-handoff.md). In particular, the CPU-only/no-fast-selector conclusion is superseded; the historical measurements themselves remain recorded.


# Context plan

## 1. The idea, restated in the repo's words

Before the main Nora turn runs, a fast decision model decides which of Nora's
already-assembled context should be exposed to the model that is about to
reason. The operator's framing:

```text
TagContext  →  ContextPlan  →  prompt projection      (preferred)
TagContext  →  delete fields  →  prompt                (rejected, correctly)
```

and two phases:

* **Phase 1** — full retrieval unchanged, dynamic *exposure* only.
* **Phase 2** — a routing substrate (message + tagger + culture + standing + a
  small transcript) assembled first, the plan decided, and only the selected
  expensive reads fetched.

The stated priority is **context relevance and cognition quality**, with
token/latency as secondary. That ordering is the thing to test first, because
the repo's measurements invert it.

## 2. What the repo already has, and why the seam is smaller than assumed

`design/context-assembly/` (DEC-273, ADR 0021) already did the hard part of this
idea. Read from the current tree:

* `nora/agent/prefill.py:122` — `_BLOCKS: tuple[tuple[str, int, BlockKey], ...]`,
  **19 rows**, name + order + cache key, one table. This is the enumerated
  vocabulary the operator's message guesses at, and it is exact, not approximate.
* `nora/agent/prefill.py:199` — `_TAG_MESSAGE_BLOCKS`, two more (`replying_to`,
  `tag_message`), built by `build_tag_message` rather than by `build_prefill`.
* `nora/agent/prefill.py:287-298` / `:977` — `_declared(...)` walks `_BLOCKS` and
  `build_prefill` renders it.
* `nora/domain/blocks.py:415` — `assemble(blocks)` drops every empty body, and
  the ordering is DATA.
* `tests/architecture/test_every_model_call_is_a_declared_occasion.py` — an AST
  test that finds every module calling a model and demands a declared non-empty
  `_BLOCKS` plus an `assemble` call. ADR 0021 records that there is **no debt
  ledger and no exception for an occasion**.

So "dynamic exposure" is not a new layer. It is a **filter over a table that
already exists**, applied at one call site
(`nora/engagement/pipeline.py:754`, `build_prefill(...)`).
`01-the-seam.md` specifies it in that shape.

## 3. The two things the measurements say

**3.1 The token half of the premise does not hold at the size the idea assumes.**

The tag prompt was measured at **avg 29,212 promptTokens** over 8 real
engagement traces (`devops/nora` → `references/system-prompt-size-analysis.md`),
split:

| part | share of prompt |
|---|---|
| six doctrine sections (system prompt) | ~72% |
| `<runtime_context>` + `<available_skills>` | ~2% |
| the per-turn context blocks + transcript | ~24%, of which the blocks are a minority |

Today's live turns agree on the shape and the scale — from Langfuse today, a tag
turn is 3–7 generations, 13–18s wall, 17k–28.5k input tokens per call. The
context blocks are the *small* term, and they are already prefix-cached at
95–99% (`nora/domain/blocks.py` `BlockKey` docstring: "The head measures 95-99%
prefix-cache hit on the first call of every tag"). A selector that removes some
of a cached, minority term is a single-digit-percent lever on cost — and it
cannot touch the 72%, because doctrine is authored and required.

**3.2 The selector costs more than the blocks it removes.**

Measured on this host today (`03-measurement-decision-models.md`):

* 11 questions against a 1.4k-char substrate = **17.6k input tokens** for ONE
  plan, because `/v1/systemone` re-reads the prompt **once per question** (input
  tokens are linear in question count: 1 question/600 chars = 260; 10 questions =
  6,200).
* Wall clock, warmed, distinct state per repetition, CPU-only: **6.0–7.6s**
  (tev1:0.8b, 752M), **8.3–12.3s** (nimble, 9B), **30.2–33.3s** (tev1:4b).

Nora's tag SLO is p95 ≤ 5s (`devops/nora`). A planning call that is 6–33s on
this box cannot sit in front of the main call, and it would *add* input tokens
rather than remove them. The one read that could pay for itself — the Honcho
`representation` read behind `peer_card`/`self_card`/`tagger_card`/
`people_in_window`, measured **p95 1,860ms** against 100–270ms p95 for every
other read (Langfuse RETRIEVER spans, 10 days) — is smaller than the decision
that would skip it.

**3.3 What the repo does agree with: Phase 1 is where the design lives, Phase 2
is where the danger lives.**

Phase 1 (exposure-only) is safe, small, and worth having — as an instrument.
Phase 2 (selective retrieval) is a change to `TranscriptInjection.assemble`'s
concurrency and to what `who_said` and `<replying_to>` can see (`02` §4). It is
not where the money is, and it is where the regressions are.

## 4. Verdict

**Build the seam as an inert instrument; do not build the selector yet.**

Stated as three claims, in order of confidence:

1. **`ContextPlan` belongs in `nora.domain.blocks`**, consumed by `build_prefill`
   as a filter over `_BLOCKS`, and it must never touch `TagContext`. (Known —
   code.)
2. **The transcript window is not selectable in the way the idea assumes**: it is
   the input of two resolvers (`<replying_to>` R-20 and `who_said` DEC-146), so
   its floor is a correctness constraint before it is a context choice. (Known —
   code.)
3. **A local decision model cannot be on the tag path on this hardware today**,
   and the JEV/Laya class the operator named is not reachable from this host
   through either Ollama cloud (`/v1/systemone` → `not implemented`) or
   OmniRoute (catalogued, but every OpenAI route answers
   `ModelProtocolUnsupported`, and there is no System One route). (Known —
   measured.)

The sequencing this forces is in `04`. The open decisions — including the one
that would change the verdict (a hosted, 70–500ms JEV/Laya call, if the operator
wants the egress and the cost) — are in `05`.

## 5. What this bundle is not

It is not a rejection of the idea. The relevance question ("does she need
`<character>` to answer *this*?") is a real question the current design answers
**statically and by room**, not per turn. This bundle says the cheapest way to
find out is to measure what is already rendered, not to put a model in front of
it — and it records what a model in front of it would cost, so the operator can
choose the expensive shape with the number in hand.
