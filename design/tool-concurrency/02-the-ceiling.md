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
class BoundedToolExecutor(ConcurrentToolExecutor):
    """Strands' concurrent executor, with a ceiling on how many run at once.

    Subclasses rather than composes, and that is forced, not preferred — see the
    CORRECTION below.
    """

    def __init__(self, *, max_concurrent: int) -> None:
        super().__init__()
        self._slots = asyncio.Semaphore(max(1, max_concurrent)) if max_concurrent > 0 else None

    async def _task(self, *args, **kwargs):
        if self._slots is None:            # 0 = unbounded = the off switch
            return await super()._task(*args, **kwargs)
        async with self._slots:
            return await super()._task(*args, **kwargs)
```

> **❌ CORRECTED 2026-09-23 — the original sketch was UNBUILDABLE.** It showed a
> compositional wrapper holding `self._inner = ConcurrentToolExecutor()` with the
> comment “delegate to `self._inner`, with acquire/release around the task”.
> **That seam does not exist.** Verified by reading
> `strands/tools/executors/concurrent.py` in full: `_execute` creates **all** tasks
> itself via `asyncio.create_task(self._task(...))` at `:54-71` and drains them
> through `task_queue` / `task_events` / `stop_event`. A compositional wrapper can
> only wrap the **whole batch**, so it could acquire once and bound nothing, or
> chunk the batch (which §2 of this document already rejects). **A wrapper cannot
> acquire per task.**
>
> The two shapes that DO work, and the trade they make:
> - **(a) subclass `ConcurrentToolExecutor` and override the private `_task`** —
>   what this document now specifies. Acquire in the task, release in its `finally`;
>   all tasks are still created up front so ordering and `stop_event` are inherited
>   unchanged. **Cost: it binds Nora to a private method of a pinned SDK.** That
>   dependency must be stated and checked on upgrade (— `strands-agents` pin in
>   `pyproject.toml`); if upstream renames `_task`, this breaks at upgrade time, and
>   test A (§4) is what catches it.
> - **(b) copy `_execute`'s drain loop** with an acquire added — rejected: it is
>   exactly the duplication this section forbids itself.
>
> (Flagged by 06-review-round-1 §B1. The `max_concurrent > 0` clause also answers
> §M5: **0 is now the explicit off switch**, which the original clamp
> `max(1, max_concurrent)` made impossible.)

### The construction sites — all of them, not just the factory's (CORRECTED 2026-09-23)

The first version said the executor is injected at “both `Agent(...)` sites”
(`factory.py:402`, `:496`). Those two are correct and `grep tool_executor nora/` is
clean — **but the repo constructs `Agent(` in four places, not two** (06-review-round-1 §M6):

| site | tools | ruling |
|---|---|---|
| `nora/agent/factory.py:402` (`create_agent`) | full registry | **IN** — inject here |
| `nora/agent/factory.py:496` (`create_dream_agent`) | full registry | **IN** — inject here |
| `nora/agent/structured.py:172`, `:248` | `tools=[]` | **OUT, with reason** — cannot emit tool calls, so a ceiling is meaningless |
| `nora/compute/smith/agent.py:223` | **seven real tools** | **UNRESOLVED — must be ruled on** |

**Smith builds an `Agent` with `run_python`, `register_artifact`, `render_mermaid`,
`render_markdown`, `render_map`, `render_html_report`, `geocode_address` and no
executor.** If Smith runs in-process, this bundle's “one limit for ALL tools” is
false the day it ships. Smith appears to carry its own vendored `.venv`, which
*suggests* a separate runtime — but that is **inference, not proof**, and this
document did not mention Smith at all. **It must be resolved before merge.**

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
`dreamer:fact-adjudication`, which the traces show reaching batches of 16 (⚠️ **see the correction below — this adaptor is DELETED** — under
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

> ⚠️ **CORRECTED 2026-09-23 — the 16-call batch's adaptor no longer exists.**
> `nora/agent/dreamer.py:826` and `:838` both describe
> `fact-adjudication.md` as **"(since deleted)"**, and `:882` refers to "the deleted
> `fact-adjudication`". So the observed 16-call batch is a **historical** observation
> from the 2026-08-28 measurement, not a property of a currently-registered adaptor
> — and it was headlining this section and `05 §3`'s first delivery obligation.
> (06-review-round-1 §m4.)
>
> **What must happen before this anchors a decision:** identify which *currently
> registered* Dreamer adaptor can still emit a large batch, and re-measure it. If
> none can, the 16 is not evidence for the ceiling's default on the dream path and
> should be marked historical with the surviving path named instead. **The ceiling's
> justification does not collapse** — the room-facing `media_fetch` batches (2–3) and
> the 76-call code-comprehension fan-out are independently observed — but the
> specific number 16 must not stand as a live risk.
