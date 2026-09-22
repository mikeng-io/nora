---
type: design
title: "Tool Concurrency — one shared, inheritable ceiling on how many tools run at once"
description: "Strands fans out every tool_use block with no bound, and Nora never overrides it. A wrapper executor adds one semaphore beneath the existing per-tool budget ceiling."
tags: [nora, agent, tools, concurrency, executor, strands, budget]
timestamp: '2026-09-23'
edges: []
---

# Tool Concurrency

## Why

Nora's agent loop runs on Strands, whose default execution strategy fans out every
`tool_use` block a model emits in a single turn. Nothing in Nora overrides it, and
the library's concurrent executor has no ceiling of its own — one `create_task` per
block, however many blocks there are.

This was not accepted as a reading of the code. It was checked against the live
Langfuse store, per the operator's instruction, and the traces make it concrete:
across 51,452 observations (`2026-07-12` → `2026-09-22`), **26.2% of turns emit
more than one tool call, 159 batches exceeded four, 16 exceeded eight, and the
largest single batch was 76 calls emitted within 104 ms of each other.**

The operator's question was the motivation, and it is sharper than "is this
possible": «我冇可能假設個 budget 之後 raise 到 10，佢就同我 10 個 concurrent 一齊
run 㗎？» — if the budget rises to ten, must ten calls run at once? Today the answer
is yes, unboundedly, and the budget is not the thing that would stop it.

## What this change is

**One ceiling on simultaneous tool execution, shared by every tool, inherited by
sub-agents.** A custom `ToolExecutor` wraps the library's concurrent one and holds
one `asyncio.Semaphore`; it is injected at both `Agent(...)` construction sites
(`factory.py:402`, `factory.py:496`). It **queues** a call that would exceed the
limit rather than refusing it, so the operator's own scenario — one post with four
videos — keeps working while a hypothetical ten-call batch no longer runs ten at
once.

## What this change is not

- **Not a replacement for the per-tool budget.** `nora/agent/call_ledger.py`
  already bounds how many calls a tool may *make*, and it already fixed the parallel
  batch that made that necessary (a 2026-08-13 trace: nine `place_info` calls
  against a budget of four, 148k tokens into a 128k ceiling, an empty reply). This
  bundle bounds how many *run at once* — a different question, in a different place.
  The two compose; neither subsumes the other (02 §4).
- **Not a change to what the model emits.** Whether Nora should be prompted to
  batch more or less is a separate concern, deliberately deferred (04 §4).
- **Not a media-lane change.** `media_fetch`'s budget, its per-invocation timeout
  arithmetic, and the provider lane belong to `design/media-read-lane/`.
- **Not per-tool limits.** Operator decision: one limit, all tools.

## Out of scope

- Prompt or doctrine changes that encourage or discourage batching.
- A single global pool shared between a parent and its sub-agents (03 §6 explains
  why separate pools of the same size is the intended reading).
- Per-agent limits, or a different limit for dream runs than for tag turns.
- Any change to `CallLedger`, `NATIVE_TOOL_BUDGETS`, or the refusal message a model
  receives when it exceeds a budget.

## Reading order

1. `01-the-hole-in-the-executor.md` — the mechanism, and the Langfuse evidence that
   it is live (including the independent reproduction of the 2026-08-13 incident).
2. `02-the-ceiling.md` — the wrapper, why a semaphore rather than chunking, the
   limit's value, and the two composing ceilings.
3. `03-sharing-and-inheritance.md` — shared across tools, inherited by sub-agents;
   the contextvar mechanism and the repo precedent for it.
4. `04-measurement-and-tests.md` — what the build owes, what is measured, what is
   deliberately not, and the residual risks.
5. `05-decisions-and-index.md` — the decision, the supersession record, the delivery
   obligations, and the deferred list.

## Open questions

1. **The value above 4.** Not decidable now; needs traffic under the ceiling. A
   saturation counter (04 §3) is the instrument, and raising it is a config change.
2. **Whether `dreamer:fact-adjudication` warrants a lower bound than a tag turn.**
   The traces show it reaching batches of 16; it is also a background run, where
   latency matters less and upstream politeness matters more. Deferred as per-agent
   rather than decided, because there is no measurement for it yet.
3. **Concurrency is derivable from existing spans** (`execute_event_loop_cycle`
   parents every `TOOL` span with start/end times), which means the verification in
   04 §2 A can be re-run against production traffic. Whether that becomes a routine
   job or a one-off check is a delivery question, not a design one.

## Status checkpoint

**Designed, not built. No production code changed.** Evidence was gathered
read-only against the live Langfuse ClickHouse store and the `origin/master` tree.
The one cross-bundle obligation — amending `design/media-read-lane/`'s deferred list
to point here instead of carrying an open question this bundle answers — is recorded
in 05 §3.
