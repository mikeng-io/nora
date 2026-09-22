---
type: design
title: "The ceiling — one semaphore beneath the budget, shared by every tool"
description: "A custom ToolExecutor wrapping Strands' concurrent one, bounding fan-out with one asyncio.Semaphore. Count and concurrency are separate ceilings that compose; neither replaces the other."
tags: [nora, agent, tools, concurrency, semaphore, executor, budget]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-the-hole-in-the-executor, rel: depends_on, provenance: asserted}
---

# The ceiling

## 1. The shape

One custom executor, wrapping the library's, holding one semaphore:

```python
class BoundedToolExecutor(ToolExecutor):
    """Strands' concurrent executor, with a ceiling on how many run at once.

    Wraps rather than replaces: the library still decides HOW to run a batch
    (task per block, results in input order, stop_event on first failure). This
    adds the one thing it does not have — a bound on simultaneity.
    """

    def __init__(self, *, max_concurrent: int, inner: ToolExecutor | None = None) -> None:
        self._inner = inner or ConcurrentToolExecutor()
        self._slots = asyncio.Semaphore(max(1, max_concurrent))

    # ... delegate to self._inner, with acquire/release around the task
```

Three deliberate properties:

- **It wraps.** The inner executor keeps its ordering guarantee (results land in
  `tool_uses` order, not completion order) and its failure behaviour. Re-implementing
  fan-out would be a second copy of library logic to keep correct — the same
  argument `call_ledger.py`'s own docstring makes about not re-deriving a budget
  check ("Two copies of a budget check are two chances to fix only one of them").
- **It bounds simultaneity, not admission.** A queued call still runs; it just
  waits for a slot. This is the whole point — the operator's case is «budget 10,
  four videos», where the answer is not "refuse the rest" but "run them without
  running all ten at once".
- **The semaphore is process-wide per executor instance.** One executor is built
  per `Agent`, and `Agent` is built per turn (the same lifecycle `ReplyGuard`
  documents: "constructed fresh per tag, so counters reset by construction"). See
  03 for what that means for sharing.

## 2. Why a semaphore and not a slice-the-list

The obvious alternative is to process `tool_uses` in chunks of N — take N, await
them, take the next N. It is rejected because it **changes the failure semantics
for free**: with chunking, a slow call in chunk one delays the *start* of chunk
two, so a batch of 10 with one 40-second call spends 40s before the other 6 even
begin. With a semaphore, the other 6 start as soon as any of the first 4 finishes.
The bound is on simultaneity, which is the resource being protected; it should
not become a bound on throughput as a side effect.

It also wastes the parallelism that is the reason the concurrent executor exists:
`asyncio.gather`-style chunking is strictly worse than a semaphore at keeping N
in flight, which is precisely what a rate-limited upstream wants.

## 3. The limit value

Not fixed by this bundle in advance — it is a config value with a measured
justification, in the same style the rest of the repo uses. What is decided:

- **Default 4.** Chosen to match the `media_fetch` budget, so the common media
  case (a post with four videos) is not degraded by the ceiling that exists to
  protect it. This is the operator's own scenario and it must keep working.
- **Not lower without measurement.** A ceiling below 4 would make the four-video
  post slower for no proven benefit. Any value below the media budget needs a
  measurement showing the resource it protects.
- **Raising it is a config change, not a code change** — which is the operator's
  stated forward path («我覺得之後 budget 有機會要升»), and the entire reason a
  ceiling must exist before the budget rises: «我冇可能假設個 budget 之後 raise
  到 10，佢就同我 10 個 concurrent 一齊 run 㗎？»

## 4. Two ceilings that compose — neither replaces the other

This is the part most likely to be mistaken, so it is stated explicitly:

| ceiling | question it answers | where it lives | behaviour when hit |
|---|---|---|---|
| **budget** (`CallLedger`) | how many calls may this tool MAKE this turn? | `nora/agent/call_ledger.py`, per-tool, from `NATIVE_TOOL_BUDGETS` | **refuses** the call; the model is told it already spent its budget |
| **concurrency** (this bundle) | how many may RUN AT ONCE? | `nora/agent/factory.py`, one limit, all tools | **queues** the call; it runs when a slot frees |

They are independent, and each closes a hole the other cannot:

- Budget alone → a batch of 10 admitted under a budget of 10 starts 10 at once.
- Concurrency alone → a model can still make 40 calls in a turn, four at a time,
  each re-paying the full context; the token ceiling arrives just as surely, only
  slower.

So the count ceiling **stays exactly as it is**. This bundle adds a bound beneath
it and changes nothing about `CallLedger`, the budgets, or the refusal messages.
That separation is the reason `CallLedger` already keeps `counts` and `_in_flight`
as two counters rather than one ("One number cannot mean both 'this ran' and 'this
is running'"); concurrency is a third question, and it gets its own answer in its
own place.

## 5. Where it is injected

Both construction sites, or the hole stays open on one path:

```python
# nora/agent/factory.py:402  — create_agent  (tag turns)
# nora/agent/factory.py:496  — create_dream_agent (dream runs)
return Agent(
    model=model,
    ...
    tool_executor=executor,   # <- added
)
```

Injecting only at `create_agent` would leave every dreamer sub-agent — including
`dreamer:fact-adjudication`, which the traces show reaching batches of 16 — under
the unbounded default. Both sites, same executor construction, one helper.

## 6. Non-goals

- **Not** a `SequentialToolExecutor` switch. Sequencing every batch would forfeit
  a real benefit (the concurrent path is why a multi-tool turn is not serial), and
  the operator's own scenario depends on the fan-out working.
- **Not** per-tool limits. Operator decision: «當然 per tools 我唔使，我覺得係所有
  tools». A per-tool scheme is a wider config surface for a case not yet observed —
  no data shows `media_fetch` needing a different bound than `web_search`.
- **Not** a change to the model's batching behaviour. See 04 §4 for why that is
  deliberately deferred.
