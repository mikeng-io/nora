---
type: design
title: "The Loop — What One Turn Is, and How It Ends"
description: "Termination needs a machine-readable signal or the loop runs to its cap every time; an unbounded sweep spends the output budget before the terminal act, discarding the whole run. Both are measured failures in a sibling project, and both are the Dreamer's failure exactly."
tags: [nora, dreamer, loop, termination, partial-progress, separation-of-powers]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
  - {dst: 08-state, rel: depends_on, provenance: asserted}
---

# The Loop — What One Turn Is, and How It Ends

This is the node the bundle is named for, and it was missing. Rather than invent
a loop, it takes the shape from a sibling project that **built** one, hit two
failures, and recorded the fix. §5 is explicit about which parts of that are
running and which are design-only, because copying an unbuilt idea while
believing it proven is the failure this bundle otherwise documents.

## 1. What one turn is

A turn is: **the model is given a situation, may reach for what it needs, and
ends by performing a terminal act that the runtime can recognise without reading
prose.**

The Dreamer has the first and second missing for four of five participants
([03](03-no-agent-loop.md)), and — more importantly — **has never had the
third**. Its stopping condition is *"the model declines to call anything"*,
which is the absence of a signal rather than the presence of one. With no tools
that absence is true on the first pass; with tools it is indistinguishable from
a model that ran out of room.

## 2. Termination — the finding that changes the design

A sibling project built an advisory review loop with a round cap. It recorded,
from live runs:

> *without some machine-readable convergence signal, the round loop exhausted
> the cap on every run regardless of draft quality.*

**A loop whose termination depends on interpreting prose does not terminate
early. It runs to its ceiling, every time.** The cap stops being a safety bound
and becomes the actual behaviour — and the cost is paid on every run, including
the ones that were finished after one pass.

Their fix, and it is the one this design adopts:

- **The terminal act is a structured call, not a sentence.** The reviewer must
  call a verdict tool as its final action. Absence of that call is a *detectable
  state*, not something to be inferred from what the text seems to say.
- **Absence fails closed.** A reviewer that never calls the verdict tool raises,
  and is treated exactly like any other failure — *"REVISE, never an implicit
  PASS."* This is R-27 applied to loop control: «I could not tell» is not «it
  passed».

For the Dreamer this means the envelope should not be *parsed out of the final
text* — it should be **the terminal call**, and its absence should be a
recognised outcome rather than a parse failure that happens to look like one.

## 3. The output-limit trap — the same failure, already solved next door

The same project's critic prompt carries this instruction, and the reason is in
the comment beside it:

> keep findings tight … *an exhaustive sweep risks running past the output limit
> before you produce your verdict, **which discards your whole review**.*

That is the Dreamer's production failure, exactly: the model spent its entire
output allowance and never reached the terminal act, so everything it had done
was thrown away.

Two things follow, and neither is a prompt tweak:

1. **The terminal act must be reachable.** If the work can plausibly consume the
   budget before the model gets to it, the budget is not a ceiling — it is a
   coin flip. Bounding what the model is asked to produce is part of making
   termination possible, not a separate concern about verbosity.
2. **The instruction must say so, with the consequence.** Not "be concise" but
   *"an exhaustive sweep discards your whole review."* The Dreamer's objectives
   state no expected output size anywhere ([13](13-doctrine.md) §4).

## 4. Partial progress — the ratchet is a loop property

Today, on timeout or budget exhaustion, **everything the loop learned is
discarded** and the watermark is held. The batch is re-read next tick, larger,
and fails harder ([01](01-live-state.md) §3).

The fail-closed half is right and must stay: a run that did not read the batch
must not advance past it. What is wrong is that *nothing survives* — a run that
settled three claims and then timed out on the fourth is recorded identically to
one that never started.

**The design: a bounded run may commit what it completed, and hold the watermark
for what it did not.** That requires the unit of progress to be smaller than the
run, which is a real change and the reason this is stated here rather than
assumed. Without it, every fix to the loop's budget is a fix to how *far* it
gets before losing everything.

## 5. What is built next door, and what is not

Stated plainly, because the distinction is the point:

**Built and running:** the advisory loop — parallel critics with distinct
lenses, a judge issuing pass/revise, a round cap, structured-output verdicts,
and a separate deterministic gate with its own bounded retry that **refuses to
commit** on failure. The two measured failures in §2 and §3 come from that
running system.

**Designed, not built:** the six-role adaptation of it — a deterministic
detector, an LLM diagnostician proposing operations, an author executing them,
then the critics/judge/gate above. Its role table and rejection zones are a
design document, not a shipped pipeline.

So: **the loop shape is proven; its application to curation is not.** This node
takes the shape and the two failures. It does not take the six roles, because
the Dreamer does not need a proposer and an executor as separate agents — it has
one adaptor and a runtime that disposes.

## 5.0 Three protections, and only one bounds a non-failing pass

A correction recorded in the note tier's spec, answering the question directly:

> *it has **three protections and only one is a loop breaker**, and it needs no
> strike because its unit of work is a cursor that always advances.*

The three are routinely conflated:

| protection | bounds | supplied by |
|---|---|---|
| re-entry lock | **concurrency** — the same unit running twice | the runtime, free |
| failure backoff | **errors** — a unit that keeps throwing | the runtime, free |
| progress breaker | **a pass that runs cleanly and achieves nothing** | **the adaptor** |

The runtime hands out the first two by construction, which is precisely why the
third's absence is invisible: a pass with no breaker still *looks* protected.
Only the third bounds a non-failing, non-progressing loop — and whether an
adaptor needs one is derivable, not a judgement
([06-trigger.md](06-trigger.md) §3.2).


## 5.1 Two things Nora's own note tier already specifies for this seam

**Containment for the failure that is actually killing runs.** The revisit
adaptor's spec requires that fetched web content is **distilled into a revision
rather than left prompt-resident** — and states the reason as a property, not a
preference: *"a note body is bounded and a fetched page is not."*

That is a direct containment for context and output exhaustion — §3's failure —
and it is stronger than a token cap because it bounds the shape rather than
trimming the size. Nothing in the fact tier does this: a fetched page stays in
the loop's context for every subsequent turn, growing the input the model must
re-read each time.

**An objective can come from the data, not only from a file.** Both existing
adaptors take a static objective from the corpus. The revisit adaptor takes its
five fields **from the note it is revisiting** — the objective *is* the note's
stated uncertainty, and the success criterion *is* its closing condition.

That is a materially different loop shape: one adaptor, an unbounded number of
objectives, and a termination test supplied per run by the data. It carries its
own acceptance test, which is the sharpest line in that spec:

> **there is exactly one revisit adaptor.** The day a second is written for a
> *kind* of note is the day it became the hardcoded scenario engine this
> constraint exists to prevent.

This bundle does not design that adaptor. It records that the loop seam must not
assume a per-adaptor static objective, because the most sophisticated adaptor in
the corpus does not have one — and a runtime that only supports file-backed
objectives would force exactly the hardcoded scenario engine that spec forbids.


## 6. What transfers, and what would be wrong to copy

**Transfers.**

- **Termination is a structured act whose absence is detectable and fails
  closed.** §2. The single most valuable thing here.
- **Two loops with different semantics.** An *advisory* loop that is bounded and
  **proceeds anyway** at its cap, surfacing the real verdict in a non-gating
  field; and a *mechanical* loop that is bounded and **refuses**. The Dreamer
  currently has one loop and treats every failure the same way.
- **The mechanical check must not negotiate.** *"If the denial depends on the
  model's mood, memory, or confidence, it is not enforcement."* The Dreamer's
  equivalents — the screen, the typed parse, the sink — are already deterministic
  and this says keep them that way.
- **Retrieval as a precondition of authority**, not a convenience: *"before the
  author revises a principle, it must retrieve the evidence chain. No retrieval
  means no valid action."* That is [08-state.md](08-state.md)'s rule arriving
  from the other direction, and it is stronger — the read is not merely
  permitted, it is required before the write is allowed to count.
- **Parallelism has a measured shape.** They use a plain gather with a small
  batch and record *why not* the framework's own swarm primitive: it silently
  ran one of four nodes. A silent partial fan-out is the failure this bundle's
  §2 is about, in a different costume.

**Would be wrong to copy.**

- **The six roles.** Separation of powers between proposer and executor solves a
  problem the Dreamer does not have — its model never writes anything. The
  propose/dispose split is already the separation, and it is enforced by there
  being no write verb at all. Adding roles would add cost and cross-agent
  handoff for a boundary already held structurally.
- **Advisory review of the Dreamer's output.** Their critics judge prose for
  publication, a subjective standard their own docs record as not reliably
  converging. The Dreamer's output is a typed envelope with a three-state
  verdict; it has a *mechanical* check available, and reaching for a subjective
  one where a decidable one exists is the wrong direction.
- **The escalate-to-a-human zone.** Their yellow zone pauses for a person. The
  Dreamer runs unattended by definition; a background loop that waits on a human
  is a loop that stalls silently. Its equivalent is to **refuse and count**, not
  to escalate.

## 7. What this seam does not decide

The turn and token numbers. Every one of them waits on the measurement in
[16-open-items.md](16-open-items.md) §0 and §1 — and §0 is the sharper
dependency: if the transport does not honour tool calls or carry reasoning
between turns, then §1's shape is wrong rather than merely unsized, and the
structured terminal act in §2 is the first thing that would break.
