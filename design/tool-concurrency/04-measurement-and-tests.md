---
type: design
title: "Measurement and tests — how the ceiling is proven to hold"
description: "The ceiling must be provably binding: a batch larger than the limit must never exceed it in flight, ordering must survive, and the pre-existing count ceiling must be unchanged."
tags: [nora, tests, concurrency, measurement, langfuse, observability]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-the-ceiling, rel: depends_on, provenance: asserted}
---

# Measurement and tests

## 1. The rule this bundle is held to

The repo's own standard, applied here verbatim: **a design claim that can be
measured is not accepted from a code comment.** This bundle's central evidence was
taken from Langfuse rather than from `call_ledger.py`'s docstring, at the
operator's instruction, and the docstring's figures were independently reproduced
before being cited (01 §3). The build owes the same standard in reverse: the
ceiling is not "done" because the semaphore is in the code.

## 2. Tests the build owes

**A. The bound is real — a batch larger than the limit never exceeds it in flight.**

The load-bearing test. Build a fake tool that records its own concurrency and
blocks on a gate; drive one turn that emits `limit + 3` tool calls; assert the
observed maximum simultaneous executions is exactly `limit`, never more. Asserting
only "all calls completed" would pass with no ceiling at all.

**B. Ordering survives.**

The concurrent executor's contract is that results land in `tool_uses` order, not
completion order (`concurrent.py`, `task_results[task_id]`). A batched tool whose
later calls return first must still yield results in input order. Wrapping a
library is exactly where this gets broken silently.

**C. The count ceiling is unchanged.**

A regression guard for 02 §4: with the executor in place, a batch exceeding a
tool's budget is still **refused**, with the same message the model receives today.
Two ceilings changing at once would make a future failure ambiguous.

**D. Inheritance.**

A sub-agent constructed inside a parent's turn reads the parent's limit rather than
the default. Test by constructing a child under a non-default limit and asserting
the child's executor observes the parent's value — the operator's explicit
requirement, so it needs a test, not an argument.

**E. A path that never sets the contextvar still gets a bound.**

The failure mode of a contextvar design is an unset default that means "unbounded".
Assert the fallback is the configured value, not `None`/`inf`.

## 3. Observability

- **A saturation counter.** How often a call had to wait for a slot. Without it
  the limit's value cannot be tuned — only guessed. Same reasoning the repo already
  applies to `nora_tool_calls_total`'s companions.
- **Per-turn max observed concurrency.** The ceiling's counterpart to the batch
  distribution in 01 §2. This is what makes the value reviewable against real
  traffic rather than against the scenario that motivated it.
- **Langfuse already carries the raw material**: `execute_event_loop_cycle` spans
  parent every `TOOL` span, and start/end times are recorded per span, so
  concurrency is derivable retroactively without new instrumentation. The metric
  above is for routine visibility, not for the one-off verification — which means
  the verification in §2 A can be re-run against production traffic at any time.

## 4. Explicitly NOT measured or changed here

- **Whether the model should batch more or less.** The operator's question is
  bounded by the executor, not by the model's behaviour, and changing emission
  behaviour would be a prompt/doctrine change with its own risk (the 148k-token
  turn was a *batching* failure, but it was a failure of the count ceiling, which
  is already fixed). Deferred deliberately: bounding what happens is one change;
  changing what the model emits is another.
- **The right value above 4.** Any raise needs traffic under the ceiling first —
  that is what §3's saturation counter is for. Raising it now would be the same
  error as picking the timeout from a projection, which `design/media-read-lane/04
  §3b` records having made twice.
- **A single global pool across parent and child.** See 03 §6; a separate decision
  with a different tradeoff (a dream run competing with a live reply).

## 5. Residual risk stated up front

- **The wrap must not swallow the stop signal.** The inner executor uses a
  `stop_event` to abandon remaining calls when one fails. If the wrapper awaits
  slots naively it can stall a batch that should have stopped early. This is the
  single most likely implementation defect and §2 C is its guard.
- **A contextvar is not a lock.** It carries a number; correctness of the bound
  rests on the semaphore, which is per-executor. If a future refactor ever shares
  one executor across agents, 03 §6's separate-pools property is lost and the
  inheritance semantics change with it — worth an assertion in the config/identity
  test suite rather than a comment.

## 5b. Test F — fail-fast abandonment (ADDED 2026-09-23)

> **Why this was missing:** the first version of this document named the
> stop_event/naive-await risk as *"the single most likely implementation defect"* and
> pointed at **§2 C as its guard**. But §2 C asserts that budget-refusal **messages**
> are unchanged — it does not assert that a failing call abandons its queued
> siblings. **The behaviour the bundle itself calls most fragile had no test.**
> (06-review-round-1 §M7.)

**Test F — a failing call abandons the calls still waiting on a slot.**

With the ceiling held at 1 and a batch of three calls where the FIRST raises,
assert that:

1. the second and third **never executed** (instrument the tool, not the log — a
   counter the tool itself increments);
2. the first call's exception **propagates** (it is not swallowed by the `finally`);
3. the **slot is released** on the failure path — a following call acquires it
   without deadlock (this is what catches a missing `finally`).

**Why it matters:** `ConcurrentToolExecutor._execute` cancels every task in its
`finally` (`concurrent.py:87-90`). A subclass overriding `_task` adds an
acquire/release around it, and the natural mistake — acquiring outside the
try/finally, or catching the exception to "tidy up" — either leaks a slot
permanently or silently converts a hard failure into a batch that runs to
completion. **Holding the ceiling at 1 is what makes it observable**: with a higher
limit the abandoned calls would simply have started first and the bug would hide.
