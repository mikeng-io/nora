---
type: design
title: "tool-concurrency — independent adversarial review, round 1"
description: "Independent read-only review of the tool-concurrency bundle at 9f26c30e. One BLOCKING finding: the wrap mechanism as specified cannot be built — Strands' ConcurrentToolExecutor creates all tasks internally with no per-task seam, so 'delegate to self._inner' cannot acquire around the task, leaving only a private-API subclass or the duplication the bundle forbids itself. Seven MAJOR and six MINOR below."
tags: [nora, tools, concurrency, review, adversarial, blocking]
timestamp: '2026-09-23'
edges:
  - {dst: 02-the-ceiling, rel: reviews, provenance: asserted}
  - {dst: 03-sharing-and-inheritance, rel: reviews, provenance: asserted}
---

# Independent adversarial review — round 1 (fresh reviewer, 4C axes)

**Reviewer:** independent sub-agent, read-only. Every finding was re-verified by
the operator's agent against the named artifact — marked ✅ where done.

> **Status: OPEN — recorded, not resolved.** B1 changes the *implementation shape*
> and therefore the deliverable; that is an operator decision, not a reviewer's.

---

## BLOCKING

### B1. ✅ CONFIRMED — the wrap mechanism as specified cannot be built.

`02 §1` (lines 19-31) sketches `BoundedToolExecutor` holding
`self._inner = inner or ConcurrentToolExecutor()` with an elided comment
`# ... delegate to self._inner, with acquire/release around the task`.

**Verified by reading the SDK in full** (`strands/tools/executors/concurrent.py`,
strands 1.50.2): `_execute` creates **all** tasks itself via
`asyncio.create_task(self._task(...))` at `:54-71`, then drains them through
`task_queue` / `task_events` / `stop_event`. **There is no per-task seam a
compositional wrapper can reach.**

Only two implementations preserve the bundle's three required properties:

- **(a) subclass** `ConcurrentToolExecutor` and override the **private** `_task` to
  acquire/release around `super()._task(...)` — which contradicts the bundle's own
  load-bearing claim "It wraps… Re-implementing fan-out would be a second copy of
  library logic" (`02 §1`), and welds Nora to a private method of a pinned SDK; or
- **(b) copy** `_execute`'s drain loop (task_queue, task_events, stop_event,
  finally-cancel) with an acquire added — precisely the duplication the bundle forbids itself.

**"Delegate to `self._inner`" cannot acquire around the task at all.** An
implementer following the sketch ships either chunking (`02 §2` itself rejects this)
or something that silently drops one of the three properties.

**Fix:** rewrite `02 §1` around the real shape — subclass overriding `_task`,
acquire in `try`, release in `finally`, tasks still all created up front — and state
the private-API pin risk explicitly (`strands-agents==1.50.2`).

---

## MAJOR

### M1. ✅ CONFIRMED — `05 §2` repeats the exact inference the audit retracted.

`05:68` (verified): *"parent `cc333f839bcf8ccc`, whose children span 128,776 ms
end-to-end — **they genuinely ran together**."* That is the corrected-away claim —
the widest child (128,730 ms) **is** that span, so the span proves nothing about
concurrency. `01` now carries the correction and rests on start-clustering +
overlap, but **the decision record carries the retracted evidence.** The artifact
that survives merge is the wrong one. Fixed below.

### M2. ✅ CONFIRMED — `02` and `03` contradict each other on where the limit comes from.

`02:27-29`: `def __init__(self, *, max_concurrent: int, ...)` — a **constructor
parameter**. `03 §3`: "the executor reads the **CURRENT value, not a captured one**"
from a contextvar. Both cannot be true.

Worse, `03 §3`'s selling point — "no signature change, no new required argument at
any call site" — is false under this repo's own contracts
(`factory.py:18-19` "Takes explicit scalars, never `nora.config` objects";
`agent_runner.py:113` "no config object below"). A contextvar `.set()` at process
startup does **not** propagate into per-turn tasks; the value must reach a per-turn
`set()` site as a threaded scalar — i.e. the signature change `03` claims to avoid.

**Fix:** pick one mechanism and show the actual `set()` site in the real call graph.

### M3. ✅ CONFIRMED — the bound is per-agent; the operator's question was system-wide.

`03 §6` concedes parent and each sub-agent get **separate pools of the same size**.
Worst case verified plausible: two tags overlap (`agent_runner.py:175`) plus
concurrent background dream runs, each with a pool of 4 → **4×N simultaneous tool
calls process-wide**.

The operator asked «budget raise 到 10，佢就同我 10 個 concurrent 一齊 run?» — this
answers *per agent* (no single agent runs 10; it runs 4), not *at all* (4×N can
exceed 10). The disclosure lives only in `03 §6`; `00`'s headline reads system-wide
with no qualification. And `01 §5`'s upstream-politeness rationale ("a fan-out of N
calls hits one provider at once", OpenCode Go measured at ~3) is **not protected
across agents**: 5 agents × 4 = 20 concurrent `media_fetch`es.

### M4. — queue-don't-refuse collides with existing wall-clock budgets, unaddressed.

Dream runs wrap the whole invocation in `asyncio.wait_for(..., timeout=...)`. With
limit 4, a 16-call batch queues 12 → roughly 4× wall time → a run that previously
completed can now die as `TimeoutError`. `00`'s open question 2 says "latency matters
less [for background runs]" — false when latency is capped by a hard timeout. Neither
`02 §3` nor `04` analyses ceiling-induced latency against any existing budget. *(This
one was not independently re-verified — recorded as the reviewer's finding.)*

### M5. — no off switch, no rollback path.

`02 §1` clamps `asyncio.Semaphore(max(1, max_concurrent))` — **no config value
disables the ceiling** (0/negative → 1, never ∞), and `04 §2 E` enshrines
"never None/inf" as a test requirement. For an operator who values reversible steps,
production misbehaviour leaves "set it absurdly high" as the only mitigation.
**Fix:** define an explicit disabled value (e.g. `0 = unbounded`) plus a one-line
rollback note in `05 §3`.

### M6. ✅ CONFIRMED — "both construction sites" is a falsified completeness claim.

`factory.py:402` and `:496` are indeed the two factory `return Agent(` sites and
`grep tool_executor nora/` is clean. **But the repo constructs `Agent(` elsewhere**
(verified by grep):

- `nora/agent/structured.py:172` and `:248` — `tools=[]`, so should be
  **excluded-with-reason**, not ignored;
- **`nora/compute/smith/agent.py:223`** — builds an `Agent` with **seven real tools**
  (`run_python`, `register_artifact`, `render_*`, `geocode_address`) and no executor.

If Smith runs in-process, DEC-196's "one limit for ALL tools" is false the day it
ships. The bundle mentions Smith nowhere. *(Whether Smith is in-process was NOT
resolved — recorded as open. It appears to have its own vendored `.venv`, which
suggests a separate runtime, but that is inference, not proof.)*

### M7. ✅ CONFIRMED — the named top defect is guarded by a test that doesn't test it.

`04 §5` calls the stop_event/naive-await risk "the single most likely implementation
defect and §2 C is its guard." But `04 §2 C` tests that budget-refusal **messages**
are unchanged — nothing in tests A–E asserts "first call fails → remaining queued
calls are abandoned". The most fragile behaviour has no test. **Fix:** add test F.

---

## MINOR

- **m1.** Evidence framing — the 76-batch correction in `01` is honest, but `00` and
  `05 §2` still lead with "the largest single batch was 76" without the
  code-comprehension caveat. Also the bucket rows sum to 9,998, so "26.2% of turns"
  means **26.2% of tool-emitting generations**, not of all turns; and the 100 ms
  clustering threshold is presented as method with no note that a different threshold
  yields different buckets — or that fast *sequential* tools cluster identically.
- **m2.** ✅ CONFIRMED — `debrief` is listed as an at-risk sub-agent (`03 §2`) but runs
  through `ask_text`, which builds `Agent(tools=[])` (`structured.py:49/:172`); it
  cannot emit tool calls and inflates the exposure surface.
- **m3.** Delivery obligation 2 is stale — `03 §5`/`05 §3` say media-read-lane
  "currently carries" the UNTESTED deferred item, but the media branch already
  amended it (`ee2855c8`).
- **m4.** `dreamer:fact-adjudication` headlines `02 §5` and `05 §3` obligation 1, but
  `dreamer.py:826/838` call it **"since deleted"**. ✅ CONFIRMED — the quoted text is
  explicitly retrospective. Verify the adaptor still exists before it anchors an obligation.
- **m5.** The saturation counter (`04 §3`) conflates contention with back-pressure: a
  slot is held across `_task`'s `task_event.wait()` gaps, so waits ≠ ceiling
  saturation. A wait-duration histogram is the tunable instrument.
- **m6.** The contextvar has, today, no production edge to inherit across — tag and
  dream roots are top-level loops, and the only agents built inside a turn's context
  have no tools (m2). Sound Python, but the property is **prophylactic, not
  load-bearing**; the bundle should say so.

## What the bundle gets right (reviewer's own words)

- **The DEC-196 claim is free and the DEC-195 collision analysis is correct** — master's
  index ends at DEC-195 (`722d56be`), `722d56be` is not in the media branch.
- **The unbounded-executor claim is true** — `concurrent.py` has exactly one
  `create_task` (`:56`) and no Semaphore/limit; `agent.py:462` default confirmed;
  factory never passes `tool_executor`.
- **The count-vs-concurrency distinction (`02 §4`) and the CallLedger composition
  framing are correct**, and reserve/release-by-toolUseId history checks out.
- **Test A** (assert observed max concurrency == limit, not merely completion) is the
  right load-bearing test.

## What the reviewer did not check

`call_ledger.py:88-131` contents; the ReplyGuard lifecycle quote; whether Smith runs
in-process; whether tag turns carry their own wall-clock timeout; the pre-amendment
text of media's deferred list; Langfuse re-derivation (per brief).
