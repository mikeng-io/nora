---
type: design
title: "The hole in the executor — one create_task per tool_use, and no ceiling above it"
description: "Strands' default ConcurrentToolExecutor fans out every tool_use block with no bound. Nora never overrides it. Langfuse shows 26.2% of generations emit more than one tool call, 159 batches exceeded four, and one emitted 76."
tags: [nora, agent, tools, concurrency, strands, executor, langfuse]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# The hole in the executor

## 1. What the executor actually does

`Agent.__init__` defaults the execution strategy to a concurrent one and nothing in
Nora overrides it:

```python
# .venv/lib/python3.13/site-packages/strands/agent/agent.py:462
self.tool_executor = tool_executor or ConcurrentToolExecutor()
```

```bash
$ grep -n "tool_executor" nora/agent/factory.py
# (no output)
```

Both `Agent(...)` construction sites — `factory.py:402` (`create_agent`) and
`factory.py:496` (`create_dream_agent`) — omit the parameter, so the default is in
force for every tag turn and every dream run.

That default starts one task per block, unbounded:

```python
# strands/tools/executors/concurrent.py:48-58
task_events = [asyncio.Event() for _ in tool_uses]
task_results: list[list[ToolResult]] = [[] for _ in tool_uses]
tasks = []
for task_id, tool_use in enumerate(tool_uses):
    tasks.append(
        asyncio.create_task(
            self._task(agent, tool_use, task_results[task_id], ...)
```

There is **no `Semaphore`, no `max_concurrent`, no queue bound** anywhere in that
file — `grep -nE "Semaphore|max_concurrent|limit" concurrent.py` returns nothing
but the `create_task` call itself. The number of tasks equals the number of blocks
the model emitted, whatever that number is.

## 2. This is live, not theoretical — Langfuse, read 2026-09-23

The claim was checked against the original traces rather than accepted from a code
comment (operator instruction: «你呢啲嘢會唔會 check 返 Langfuse 去 check 咗啲 trace
先？»).

**Store:** Langfuse ClickHouse (`langfuse-clickhouse-1`, db `default`, table
`observations`), **51,452 rows, `2026-07-12 14:26` → `2026-09-22 19:30`.**

Tool calls are `type='TOOL'` spans. Grouping them by `parent_observation_id` and
clustering start times within 100 ms gives one group per model turn:

| tools per turn | batches |
|---|---|
| 1 | 7,376 |
| 2 | 1,583 |
| 3 | 688 |
| 4 | 192 |
| 5 | 67 |
| 6 | 45 |
| 7 | 23 |
| 8 | 8 |
| 9 | 4 |
| 10 | 3 |
| 11 | 2 |
| 13 | 1 |
| 14 | 2 |
| 15 | 1 |
| 16 | 2 |
| **76** | **1** |

**26.2% of turns (2,622 of 9,998) emit more than one tool call. 159 batches
exceeded four. 16 exceeded eight. The maximum is 76.**

The largest batch is a real single fan-out, not an aggregate span — resolving it
directly:

```sql
SELECT type, count(), dateDiff('millisecond', min(start_time), max(start_time))
FROM observations WHERE parent_observation_id='cc333f839bcf8ccc' GROUP BY type
-- TOOL  76  spread 104 ms     <- 76 tool calls emitted within 104 ms
-- GENERATION 1                <- one model generation as the parent
```

The 76 calls were also emitted together, and they did overlap in wall time:

```sql
SELECT name, dateDiff('millisecond', start_time, end_time) AS dur_ms
FROM observations WHERE parent_observation_id='cc333f839bcf8ccc'
ORDER BY dur_ms DESC LIMIT 5
-- chat                     442,667   (the parent generation)
-- explore_codebase         128,730
-- query_conversation_memory 52,354
-- query_conversation_memory 48,318
-- query_conversation_memory 45,617
```

> **⚠️ CORRECTED 2026-09-23 (independent audit).** The first version read "the widest
> single child was far shorter — they ran together". **That inference was wrong.** The
> widest child (`explore_codebase`, 128,730 ms) *is* the 128,776 ms span — 99.96% of
> it. So the span length is **one long call's duration**, not proof that the batch ran
> concurrently.
>
> **The fan-out is still real, on better evidence:** all 76 calls *started* within
> **104 ms** of each other (a sequential executor could not do that), and at least
> **8 of them overlapped** while running 40–52 s each (the four `query_conversation_memory`
> calls plus others). What is corrected is only the sentence doing the work — the
> concurrency conclusion rests on the start-time clustering and the overlap, not on
> comparing the span to its widest child.
>
> Context the operator should have: this batch is a **2026-07-14 code-comprehension
> turn** (`explore_codebase` / `grep_source` / `list_source_units`), not an agent-loop
> media turn. It is a legitimate demonstration of what the executor permits, not of
> what a room-facing turn does.

## 3. The COUNT half is already closed; the CONCURRENCY half is not

This is the important distinction, and it is easy to mistake one for the other.

`nora/agent/call_ledger.py` exists **because of this exact failure mode**, and its
docstring records the original incident (line 13 onwards, italics added):

> A budget check that reads only `counts` holds the ceiling BETWEEN model turns
> and not WITHIN one: a model emitting its tool calls as a **PARALLEL BATCH** is
> asked about every call in the batch before any of them completes, so all of
> them see the pre-batch count and all pass.
>
> *Found in a live Langfuse trace (2026-08-13): nine `place_info` calls against a
> budget of four, in two batches, which carried the turn to 148k input tokens
> against a 128k ceiling. It ended `limit_total_tokens` with an empty message — so
> the group heard nothing*, which is R-23's shape and the worst way for a budget
> to fail.

**Independently reproduced against the traces, 2026-09-23.** Trace
`078eb948c214ffd873053657c0281e9c`, 2026-08-13: nine `place_info` spans, in two
clusters — six within **3 ms** of each other at `14:56:11.473–.476`, then three
within **2 ms** at `14:56:14.189–.191`. Two genuine parallel batches, 6 + 3,
matching the docstring. The trace's `chat` generations sum to **147,972 input
tokens** against the then-current 128,000 ceiling — the "148k against 128k" figure.

`CallLedger` fixes the count problem properly: `reserve(tool_name, ref=...)` marks
a call admitted-but-unfinished so it counts against the ceiling while it runs, and
`release` is keyed on the SDK's `toolUseId` so a vetoed sibling cannot free another
call's reservation (`call_ledger.py:88-131`; a 2026-08-21 review found the
name-keyed version let a veto reopen the ceiling).

**But `CallLedger` bounds how many calls a tool may make. It does not bound how
many run simultaneously.** Nothing does. A batch of 76 that passes the budget
check still starts 76 tasks at once.

## 4. It is already happening for `media_fetch` specifically

This settles the question `design/media-read-lane/` left open ("whether Strands
parallelises multiple tool_use blocks is UNTESTED"). It does, and it already has:

```sql
SELECT parent_observation_id, countIf(name='media_fetch')
FROM observations WHERE type='TOOL' GROUP BY parent_observation_id HAVING n>1
```

**Seven batches contained more than one `media_fetch` in a single model turn** —
four with two calls, three with three calls:

| time | calls | emit spread | wall time |
|---|---|---|---|
| 2026-08-11 00:17:04 | 3 | 3 ms | 7,870 ms |
| 2026-08-11 00:18:16 | 2 | 1 ms | 5 ms |
| 2026-08-27 08:34:56 | 2 | 1 ms | 7 ms |
| 2026-08-31 15:28:43 | 3 | 3 ms | 8,465 ms |
| 2026-08-31 15:30:18 | 3 | 3 ms | 9,363 ms |
| 2026-09-01 12:03:50 | 2 | 2 ms | 2,045 ms |
| 2026-09-01 12:09:04 | 2 | 2 ms | 11 ms |

Two readings matter, and they point in opposite directions:

- **Good news for the media lane:** a batch of three media reads completes in
  ~8–9 s wall rather than three sequential reads, so the fan-out the media bundle
  wants is *already working* for a multi-attachment post.
- **The ceiling is already being tested:** the model batch-emits media reads today,
  with **no bound above them**. The 76-call maximum is the same mechanism with a
  different tool, and `media_fetch`'s budget of 4 is the only thing limiting it —
  which is exactly why the operator's «budget raise 到 10» concern is concrete
  rather than speculative.

## 5. Why this matters beyond media

The operator's point, verbatim: «呢個唔單止係呢個要解決嘅問題，其他 tools 都需要
解決» — this is not a media problem, every tool has it.

The exposure is real for three different reasons:

- **Resource.** Each concurrent call may hold a decrypted blob, a socket, or a
  model connection. `MediaComprehension`'s own semaphore exists for exactly this
  reason and is commented as such (`nora/ingest/comprehension.py:154-156`: "the
  bound is on the COUNT of in-flight reads, because each one holds a decrypted
  blob and its base64 copy"). But that semaphore governs the **ingest** path — an
  agent-path `media_fetch` goes through `VisionClient` and never touches it.
- **Upstream.** A fan-out of N calls hits one provider at once. OpenCode Go was
  measured to serve three parallel requests without a 429 (design/media-read-lane),
  but 76 is not three, and the limit is a provider policy, not a law.
- **Cost.** This is the mode that already produced a 148k-token turn and an empty
  reply. A batch that is admitted and runs is paid for in full; the bigger the
  fan-out, the more context each call re-pays.

## 6. What this bundle does NOT claim

- It does not claim the model should stop batching. Whether to *encourage* or
  *discourage* parallel emission is a prompt question, deferred.
- It does not claim 76 is reachable for `media_fetch` specifically. `media_fetch`
  has its own budget of 4 (`nora/agent/tools.py:727`), and the guard refuses the
  fifth. The 76 is what the *executor* will run if the *guard* admits that many —
  which is tool-agnostic by construction.
- It does not re-open the count ceiling. The two bounds compose; see 02 §4.
