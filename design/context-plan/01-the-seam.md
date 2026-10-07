---
type: design
title: Historical October 5 — The seam — where a plan is built, who builds it, and where it is consumed
description: Historical October 5 finding, superseded in relevant part by nodes 06–08 on October 7.
  ContextPlan as a value in nora.domain.blocks; constructed after assemble and consumed by build_prefill
  as a filter over _BLOCKS; why it must not live in TagContext, in admission, or in EngageDecision;
  and why the existing EngageDecision.rationale seam does not become the carrier.
tags:
- nora
- context-plan
- seam
- blocks
- prefill
- engage-decision
timestamp: '2026-10-05'
edges:
- dst: 00-overview.md
  rel: realizes
  provenance: asserted
- dst: 02-what-is-selectable.md
  rel: constrained_by
  provenance: asserted
---

> **PARTLY DEPRECATED (2026-10-07).** Historical 2026-10-05 proposal and CPU measurements. The current recommendation, acceleration measurements, operator authorization and implementation state are in [06](06-current-recommendation.md), [07](07-current-measurements.md) and [08](08-experiment-and-handoff.md). In particular, the CPU-only/no-fast-selector conclusion is superseded; the historical measurements themselves remain recorded.


# The seam

## 1. The type

```python
@dataclass(frozen=True)
class ContextPlan:
    blocks: frozenset[str]          # names from prefill._BLOCKS + _TAG_MESSAGE_BLOCKS
    transcript_window: int          # <= [store] context_window
```

Frozen, total, and **closed over the declared vocabulary** — the names are the
same strings `_BLOCKS` already uses (`peer_card`, `standing`, `media_text`, …),
never a second vocabulary. That is the property that keeps this from becoming
the drift `design/context-assembly/00-overview.md` was written about: a plan
naming a block that no table declares is a bug, and
`tests/architecture/test_the_registry_is_exhaustive.py` is where that gets
caught.

Lives in **`nora/domain/blocks.py`**, beside `ContextBlock`, `BlockKey` and
`assemble`. Reasons: it is framework-free (AGENTS.md, `nora/domain/`'s rule), it
is consumed by `nora/agent/prefill.py` which already imports from there, and the
alternative — `nora/agent/prefill.py` — would put a value type in the module the
architecture test treats as an occasion.

`transcript_window` is a second field rather than a sentinel in `blocks`
because window size is not a block's presence: `<group_transcript>` is always
present, its *extent* varies. `02` §3 argues the bound.

## 2. Who constructs it, and when

**Phase 1 (this bundle's scope): code constructs it, and it is a full set.**

```text
pipeline.py:606   ctx = await self._context.assemble(request=request)
pipeline.py:607   culture = ctx.culture                       ← unchanged
pipeline.py:754   pre_fill = build_prefill(ctx, ..., plan=plan)
```

The plan is built **after `assemble` and before `build_prefill`**, from the
`TagContext` that already exists. In Phase 1 nothing is removed from retrieval,
so the plan's only job is to say which of the assembled blocks reach the prompt.

**Why not before `assemble`.** The operator's Phase 2 does move it earlier, and
`04` explains why that is the expensive version: it serialises a decision in front
of a gather that is currently concurrent
(`nora/engagement/context.py:239-254` — one flat `asyncio.gather` of six, plus
three outer). Today the tag path's *reads* are 100–270ms p95 each and hide behind
each other; the only slow one is Honcho's `representation` (1,860ms p95). Adding
`base reads → decision model → selected reads` turns that into
`base reads + max(decision) + selected reads`, and with the measured decision
costs in `03` that is strictly worse on this host.

**Why not in admission.** Correct, and for the reason the operator already
suspected: `nora/engagement/stages/admission.py:75` is documented as "a pure
decision function — it does no I/O and does not await". It owns R-9 (debounce),
R-10 (the in-flight lock), R-12 (self-tag refusal) and R-30 (the FIFO queue).
Putting an HTTP call there breaks all four rules' latency contract at once and
puts a network dependency *inside* the lock-acquisition path.

## 3. Who consumes it

Exactly one call site: `build_prefill`, which gains a `plan: ContextPlan | None`
parameter. `None` means **today's behaviour exactly** — every declared block,
full window — which is what makes the change inert until something passes a plan.

Inside `build_prefill` the plan is applied at `_declared`'s walk
(`nora/agent/prefill.py:287-298`) as a skip, not as a deletion:

```python
out = [
    built.get(name) or ContextBlock(name=name, order=order, key=key, body="")
    for name, order, key in _BLOCKS
    if plan is None or name in plan.blocks
]
```

That shape matters for two reasons:

* `assemble` already drops empty bodies, so a skipped block and an empty block
  are the same thing on the wire. The difference is only in the
  `nora.prefill.blocks_declared` / `blocks_empty` / `blocks_rendered` annotations
  (`prefill.py:1077-1084`) — which is exactly where an experiment needs to be
  visible, and which is where the *reason* for an absent block should be
  recorded (`04` §3).
* `_TAG_MESSAGE_BLOCKS` (`replying_to`, `tag_message`) are filtered by the same
  rule, and `replying_to` is a **correctness** block (`02` §2).

## 4. `TagContext` is not touched, and `EngageDecision` is not overloaded

The operator is right on both, and the code confirms it:

* `TagContext` is read downstream of the prefill, independently. From
  `nora/engagement/pipeline.py`: `ctx.tagger_card` at :708 (feeds
  `Room.tagger_card_injected`, which decides whether `about` re-fetches),
  `ctx.transcript` at :734 (passed to `build_tool_specs` for `who_said`),
  `len(ctx.notes)` at :775 (a span attribute), `culture = ctx.culture` at :607
  (handed to the debrief stage). Nulling a field to hide a block from the model
  would silently change `about`'s behaviour, blind `who_said`, and hand the fold
  a different account than the reply saw. **Filter at render; never mutate the
  context.**
* `EngageDecision` (`nora/domain/ports.py:562`) means one thing: "a tag admitted,
  route to the engagement pipeline" (its own docstring). Its `rationale` field
  exists as a seam for a *future classifier's* reasoning
  (`nora/engagement/context.py` module docstring: "the only route by which a
  future classifier's `rationale` reaches the prefill … so that seam does not
  require another signature change later"). A plan is not a rationale and a
  classifier's verdict is not a plan: `engage=True` is the gate, and *which
  blocks* is a different question asked after it. Adding a `plan` field to
  `EngageDecision` would make every existing constructor site carry a field it
  cannot fill — `nora/ingest/policy.py` alone builds it 20+ times.

`EngagementRequest` (`ports.py:584`) does already carry the whole decision into
`assemble`, so a *future* selector that wanted the classifier's rationale has a
path to it that needs no signature change. That is the seam to reuse if a
selector is ever built — not a new field on the decision.

## 5. What the seam deliberately does not carry (yet)

`03` measures a decision model and this bundle does **not** wire one. So `plan`
is `None` at the only production call site on the day this lands, and the
instrument is:

* the type and its validation (a name not in `_BLOCKS` is a construction error);
* the filter in `build_prefill`;
* the annotation that says which blocks a plan withheld, and why.

That is `04`'s step 1. Everything after it is gated on a measurement this bundle
does not have.
