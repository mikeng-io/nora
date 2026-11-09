---
type: design
title: "Execution and time — conscious turns, subconscious Dreamer work, one clock"
description: "Execution ownership, the current Cognition/Dreamer split, the target handoff, and the common tick and time-slot contract with one integrated implementation and acceptance plan."
tags: [nora, cognition, dreamer, conscious, subconscious, scheduling, reminders]
timestamp: '2026-11-10'
edges:
  - {dst: 20-observe, rel: extends, provenance: asserted}
  - {dst: 30-cognition, rel: extends, provenance: asserted}
  - {dst: 70-migration, rel: sequences, provenance: asserted}
  - {dst: ../dreamer-agent-loop/06-trigger.md, rel: revises, provenance: asserted}
---

# 18 · Execution and time

## 1. Ownership and delivery status

**The agreed architecture has two execution layers: conscious turns and
subconscious Dreamer work.** These are execution responsibilities, not additions
to the Authored/Record/Rollup/Notebook/Views data layers of [05](05-data-model.md).
This node owns their runtime boundary and common time vocabulary. [20](20-observe.md)
owns wake policy; [30](30-cognition.md) owns Cognition's work; component stores
own their state transitions. Other documents refer here instead of defining a
second clock model.

Operator direction, 2026-11-09/10: restore the conscious/subconscious division;
Cognition must actually execute as a Dreamer adaptor. A common explanation is
required even where code is not shared. No separate cron service is required.
This records the target and the implementation plan, **not shipped behavior**.
No new DEC/R number is allocated for unbuilt behavior.

| Execution layer | Owner | Input and result |
|---|---|---|
| Conscious | Existing engagement/fire turn machinery; the reminder heartbeat drives deferred turns | An addressed message or admitted occasion produces a room-bound turn and its delivery outcome. A proactive occasion may deliberately remain silent. |
| Subconscious | Dreamer runtime, with Cognition as an actual adaptor | Recorded work, evidence and due self-wakes produce bounded internal sessions, committed state and deferred acts. No direct room delivery. |

The heartbeat is a driver of the conscious layer, not its whole implementation:
tags and other admitted occasions also enter conscious turns. Conversely,
Dreamer is the subconscious runtime, not the name of every background task.
Collectors, transport reconnects and operational maintenance remain plumbing;
two execution layers do not mean two asyncio tasks or two deployed services.

### Current wiring, inspected at the design baseline

Baseline: `52d093d64721ebbb69d0ad45396231fd78f5cdb6`.
These are source observations, not claims about enabled production flags.

| Path | Actual ownership | Gap against the target |
|---|---|---|
| Cognition | `nora/__main__.py` starts `wake_loop`; `WakeEvaluator._schedule_on_wake` starts the session hook. Composition binds it to `CognitionSessionRunner.wake_hook`; the session calls `LiveCognitionAgentRunner`, which directly constructs and invokes an agent through the factory. | It shares Dreamer's factory, guards, budget types, tools and instruction infrastructure, but bypasses `DreamerRuntime.run`. |
| Other Dreamer adaptors | `dream_loop` applies eligibility and dispatches through `DreamerRuntime.run`. | Its existing interval/phase slots are reusable, but are not a universal base tick. |
| Deferred conscious turns | `HeartbeatLoop.tick` reads due reminders and runs compatible groups through the fire path. | Timestamp eligibility and sleep-after-pass cadence, rather than aligned numbered ticks. |

Source: [startup](../../nora/__main__.py),
[composition](../../nora/app/composition.py),
[wake evaluator](../../nora/app/wake.py),
[Cognition sessions](../../nora/app/cognition_session.py),
[Cognition agent runner](../../nora/app/cognition_agent_runner.py),
[Dreamer runtime](../../nora/agent/dreamer.py),
[Dreamer driver](../../nora/app/runtime.py),
[heartbeat](../../nora/app/heartbeat.py).
The three drivers are separate today; the desired consolidation is not achieved
by giving them the same names or by sharing only agent construction.

## 2. The complete flow and the handoff

```text
Addressed message / other admitted occasion
  -> conscious engagement or fire turn -> governed delivery -> recorded outcome

Observation -> committed Record/work/stimuli
  -> subconscious eligibility -> Dreamer admission
  -> Cognition adaptor's bounded session(s) -> committed internal state
       | inward: self-wake -> future subconscious eligibility
       | outward: reminder -> due tick -> heartbeat admission
       |                          -> conscious fire turn -> recorded outcome
       + new evidence and outcomes feed subsequent subconscious work
```

Cognition's comprehend/reshape/decide-and-act order and per-step committed
handoff in [30](30-cognition.md) remain. Hosting them in Dreamer does not merge
all steps into one prompt or make evidence-driven work wait for a minute tick.
The later Character/profile/Lessons consolidation remains owned by that flow;
this node adds no competing reflection adaptor or fixed roster of adaptors.

Eligibility answers whether work is ready. Dreamer owns admission, task
lifecycle, cancellation and bounded agent execution for subconscious work.
Cognition owns its agenda, permitted verbs, typed writes and session outcome.
Its evaluator may remain a pure policy component, but must not retain a second
independent task-launch/model-invocation route. A helper module can remain if
it has one owner; deleting every Cognition-specific class is not the criterion.
The adaptor's identity and unit scope accompany the run through its outcome.

Preserve the existing shared factories, context assembly, instruction loading,
scoped tool registry and budget enforcement. Extend the existing runtime seam
to host a session-shaped adaptor; do not force Cognition's typed writes into a
legacy proposal envelope or revive retired accept/commit behavior. A failed
session must retain its real failure and pending work; a runtime wrapper must
not translate a normal return into success when the session failed.

A reminder is an instruction/intent, not final text to replay. At fire time the
conscious turn re-reads its permitted situation and produces the words. An
inward self-wake cannot send, and an outward reminder does not wake Cognition
in place of delivery. Room/requester/kind boundaries, cancellation, unit
ownership, policy rechecks and the conversation concurrency permit remain.

## 3. Shared time contract

| Term | Meaning |
|---|---|
| Clock | UTC wall time supplies schedule position; monotonic elapsed time supplies running timeouts and in-process backoff. Monotonic values are not persisted deadlines. |
| Logical tick | Integer position on a fixed grid, advancing through downtime. The target base quantum is one minute. |
| Evaluation pass | An actual check of eligible work. It can observe several elapsed ticks; a logical tick is not a model invocation. |
| Schedule | A rule producing due occurrences: one-shot target, relative delay, fixed interval/phase or local calendar recurrence. Evidence gates remain separate. |
| Time slot | A schedule's indexed window. A slot may span many base ticks; it identifies an interval, not execution capacity. |
| Occurrence | A particular instance of work, with stable identity and a target tick when time-driven. |
| Admission/claim | Component-owned permission to execute now. Being due is insufficient. |
| Execution outcome | What actually happened: completed, deliberately silent, failed, unavailable or another existing typed result. Scheduled/due/claimed/started are not success. |

A **conversation slot** (`ConversationSlots`) is a concurrency permit. Always
qualify it; it is not a time slot and must not become a scheduling window.
Likewise, a Cognition unit's single-flight rule is not a time slot.

### Calculation and representation

Use integer arithmetic with the fixed Unix UTC epoch E and Q = 60,000 ms:

```text
current_tick = floor((now_ms - E) / Q)
due_tick for an absolute target = ceil((target_ms - E) / Q)
due_tick for N tick boundaries later = current_tick + N
slot(t) for interval I ticks and phase P ticks = floor((t - P) / I)
```

The origin and quantum are stable across restart and deployment. Polling
cadence may change without changing what a stored tick means. Never use a
mutable process-local increment counter or decrement every row each minute.
Never let scheduling tick settings reinterpret existing work.

For time-driven instructions the target is an absolute `due_tick`, and pending
work is eligible when `due_tick <= current_tick`. Preserve stable ordering and
bounded reads. Calendar requests resolve once into a real instant and then
round up; occurrence generation remains a calendar operation in the requested
zone, not an assumed constant number of ticks per month or local day.

A duration request means **at least that duration**: compute now plus duration
before rounding up. An explicit tick delay means boundary crossings. These are
different inputs and must be described honestly by tool results/context. Zero
tick delay can be due now; existing minimum-lead restrictions still apply where
the requesting surface has them. Time arithmetic does not widen tool grants.

Ticks replace deadline eligibility, not all timestamps. Retain calendar rules,
zone and series anchor, original requested time where needed, and real
created/started/handled times for provenance. Human and model views show useful
local time/delay and age as well as any tick identifier needed for explanation.
All model-facing blocks remain built through existing context assembly.

During migration choose one writable eligibility authority: the target is
`due_tick`; legacy `due_at` readers receive a projection or are migrated in the
same change. Do not leave independently writable timestamp and tick deadlines.
Inventory reminders, self-wakes, series generation, move/cancel tools, snapshots,
replay seeds, portal/CLI readers and context blocks before changing the schema.
Historical views must preserve the original schedule semantics and provenance.

### Evaluation, downtime and execution

An aligned driver wakes for the next relevant boundary and recalculates the
current tick after work. Work duration never advances the logical clock by
one; a slow pass may skip evaluation opportunities without losing due work.
Do not hold the clock calculation behind a global execution lock. Due work
retains component concurrency limits and bounded dispatch; a busy room may defer
its reminders without blocking subconscious evaluation.

On restart calculate the present tick and read overdue pending work. Do not
replay every empty tick. Keep the existing semantics unless a later requirement
explicitly changes them:

- A pending one-shot can be handled late; an already claimed reminder is not
  re-armed. Boot recovery resolves stranded firing rows as failures.
- Recurring reminders handle the overdue row once and generate the first future
  occurrence; skipped occurrences consume the existing finite series count.
  Claim and successor insertion remain one atomic transaction.
- A due self-wake remains subject to its existing wake policy, cancellation,
  cooldown, freshness and budgets; clock catch-up does not bypass these gates.
- An interval-only Dreamer schedule runs the current eligible slot, rather than
  replaying every missed slot. Preserve successful settlement across restart;
  `unavailable` leaves work retryable under its existing backoff. Last-success
  tracking is not an exactly-once guarantee across a crash during execution.

Backward wall-clock adjustment must not re-arm completed occurrences or reopen
consumed slots; future work waits until due again. Forward adjustment follows
the same overdue rules as downtime. Reuse stored occurrence identity and
settlement state; do not create a separate universal execution ledger. The
current Dreamer equality-only slot comparison needs review against this target.

## 4. Worked examples

| Case | Required behavior |
|---|---|
| Current tick 35; request 60 ticks later | Target 95. With 8,000 ticks the target is 8,035. No per-minute row update. |
| Request at 12:00:40 for at least one minute | Target instant 12:01:40, eligible at 12:02. One boundary later would be 12:01 and is a different request. |
| Deadlines 03:30:50 and 04:30:00 | Eligible at 03:31 and 04:30 respectively. Neither fires early; tick alignment does not collapse an hour of work into one batch. |
| Two compatible reminders share a due tick | Batch only under the existing room/requester/kind rules. Different people or kinds retain separate turns. |
| Execution crosses several minute boundaries | Recalculate present tick and leave deferred work pending; no global model call holds the clock. |
| Process stops at 40 and restarts at 100 with a pending target 95 | The row is overdue and considered once under admission. No 60 fake execution ticks. |
| Daily 09:00 across an offset change, or a monthly date | Calculate the next local-calendar occurrence from its anchor, then its tick. Preserve existing DST and month-end behavior. |
| A session or delivery crashes | Record the applicable failure; reminder at-most-once and subconscious retry semantics remain distinct. |

## 5. Cron and scope

No standalone cron service, generic job engine or arbitrary script executor is
part of this design. Existing relative, interval and calendar rules describe
the required timed work; event/evidence-driven work remains event-sensitive.
A cron expression would be another calendar-rule syntax, not a third execution
layer. Reopen syntax support only for a concrete schedule the supported rules
cannot express. Operational host jobs remain outside Nora's cognitive layers.

## 6. One integrated implementation and its acceptance

**Remaining runtime work: all items below.** This documentation change delivers
only the architectural contract and source reconciliation. The sequence is an
implementation order inside one integrated change, not a series of unmeasured
partial cutovers or a shadow week.

1. Map all producers and consumers listed in §3, including the current model
   call and durable state transitions. Preserve the existing context/occasion
   contracts and later Cognition step consolidation. Select the smallest
   runtime extension that can execute the session-shaped Cognition adaptor.
2. Route Cognition admission and execution through Dreamer; remove the sibling
   task-launch/model-invocation route. Keep wake decisions and session state
   component-owned, with one owner for task cancellation and unit occupancy.
3. Reuse/extend the existing slot arithmetic into the shared time calculation;
   migrate time-driven targets and every reader atomically with the agreed
   single authority. Preserve pending, firing, cancelled and historical rows,
   series identity, ordering and successful-commit-only settlement. Index the
   due predicate; measure read work on generated history rather than relying
   on a result limit. Keep a database backup and an explicit compatible
   recovery procedure for the deadline representation change.
4. Align the two layers' timed drivers, keeping arrival-driven work responsive.
   Remove obsolete scheduling/execution wiring and update configuration,
   templates, tool text, documentation and replay/snapshot readers together.
5. Prove the following properties before calling runtime consolidation complete.

| Obligation | Required evidence at the implementation head |
|---|---|
| Coherence: one subconscious execution owner | A real composed wake reaches the Cognition adaptor through Dreamer admission and execution. Cancellation/shutdown releases it once. A negative probe proves the old independent launch route cannot execute it. |
| Consistency: reuse the existing paths | Conscious addressed and deferred turns retain their engagement/fire paths. Synthetic input/trajectory readback shows the same intended context, tools and write permissions; no parallel prompt builder or timer store. |
| Conformance: boundaries and lifecycle survive | Positive and negative probes for room/requester/kind isolation, permission revocation, cancellation, unit single-flight, budgets, atomic successor creation and crash recovery. Due-time membership alone is not a positive control. |
| Correctness: shared time is real | Generated-clock cases for §4, same-slot successful suppression, unavailable retry, restart, backward/forward clock changes and slow work. A reverted timing/ownership fix must make its named guard fail for the intended reason. |
| Completeness: no stranded surface | Inventory every scheduling writer/reader, configuration reference, snapshot/replay seed and human/model projection; prove no independent deadline authority remains. Measure bounded due reads with growing unrelated history. |

Use focused offline checks first, then the agreed snapshot replay through the
actual composed paths with fixed measurement and provenance. Model-quality or
live runtime acceptance must be reported separately from structural tests and
needs its applicable authorized run; this design does not authorize model spend
or host changes. Existing concurrency/security/schema changes receive their
required independent review when implemented. Document delivery and runtime
acceptance remain separate states.

The architecture remains two execution layers. Reopen shared-code choices on
measured incompatibility or a smaller implementation that preserves these
properties; changing a clock helper cannot substitute for execution ownership.

### Delivery coverage at the implementation head

| Obligation | Implemented | Actually measured (named evidence) |
|---|---|---|
| One subconscious execution owner | `SubconsciousExecutor` submits a session-shaped `CognitionSessionAdaptor` to `DreamerRuntime.run`; `DreamerRuntime` owns admission, the task, cancellation and bounded execution. The sibling model path is deleted: `DreamerAgentRunner` delegates to `DreamerRuntime.run_agent`, the same `create_dream_agent`/`factory.invoke` route every adaptor's `_sample` takes. | `test_subconscious_executor.py`: a composed `_on_wake` dispatches only through `runtime.run` (positive, and the negative that nothing executes outside it); wiring refuses a missing runtime; `test_cognition_wiring.py`; `test_cognition_session_runner.py` for the preserved session contract. |
| Reuse of the existing paths | Conscious addressed/deferred turns keep `HeartbeatLoop`/`fire`; sessions keep `CognitionSessionRunner`, its tools, writes, guard vocabulary, trace and `SESSION_TOKENS` spend handoff. | `test_cognition_session_tool_specs.py`, `test_cognition_agenda_skills.py`, `test_cognition_agent_runner.py`, `test_backfill_limits_and_traces.py`, `test_turn_records.py`; no second prompt builder (the session still assembles `_BLOCKS`). |
| Boundaries and lifecycle survive | Room/requester/kind batching, CAS claim/successor insertion, `WakeGuard`, single-flight, unit lock, `CognitionShutdown` idempotent cancel-and-close unchanged. | Reminder and stimulus contract suites (`test_reminder_store_contract.py`, `test_stimulus_store_contract.py`, `test_stimulus_session_lifecycle_contract.py`) on SQL and the in-memory twin; `test_wake_evaluator.py` policy/brake cases. |
| Shared time is real | `nora.domain.ticks` is the one tick/slot vocabulary (`TICK_MS`, `tick_of_ms`, `due_tick_of_ms`, `slot_index`); `due_tick` is generated from the stored deadline (one writable authority) and indexed; `_slot` delegates; the pending read and `Watermark.consumed` compare ticks. | Fake-clock boundary cases (`test_subconscious_executor.py`, `test_wake_evaluator.py`, `test_stimulus_store_contract.py`): mid-tick deadlines eligible at the next boundary, never early; boundary-aligned deadlines in their own tick; `pending_tick`/`due_tick` parity between SQL and the twin. Backward/forward clock and restart are covered by the watermark/settle contract tests, not by a dedicated clock-skew case — recorded as a gap, below. |
| No stranded surface | Reminder and stimulus reads/writes migrated; `idx_reminders_due_tick` and re-pointed `idx_stimulus_unit_due`; snapshot/replay readers keep real instants (`due_at`, `fired_at`, `recorded_at` are projections, not eligibility). `MEMORY_WORK_ITEM`/`BRIDGE_WORK` retry queues are outside the named inventory and unchanged. | `test_stimulus_store_plan.py`: the pending read's three branches plan onto their indexes, and `EXPLAIN ANALYZE` on generated history (2k → 8k consumed rows) holds the due read at the fixed 3-row pending batch. |

**Fixed, found by the migration:** a stimulus eligible but cut by the merged
`limit` bound — behind carried/unclaimed side-door rows — was consumed by the
new watermark's id range without ever being shown or claimed. `UnitView.over`
now reports what the bound cut, and `decide` names it `unclaimed`; the
deterministic contract case (`test_a_due_row_the_merged_bound_cut_is_named_unclaimed`,
both stores) asserts the cut row is named, listed unclaimed and never consumed
unseen, and the contract fuzz loses nothing on the seeded runs.

**Mutation proof:** `scripts/mutations/conscious-subconscious-time.json` —
`make mutate` reports `ALL MUTATIONS CAUGHT` (4/4 RED then green): the
`session_run` dispatch, the executor's `runtime.run` submission, the
`view.over` unclaimed clause and fake-store tick eligibility. The twelve
entries this change re-anchored in `cognition-input`, `cognition-measures`,
`cognition-step1-signals`, `cognition-step3a2-session` and
`cognition-step3a3-wiring` were re-proven at their new anchors: `ALL
MUTATIONS CAUGHT` (14/14).

**Not measured here (honest limits):** live/model runtime acceptance (needs its
authorized run — this PR authorizes none); a dedicated clock-skew/backward-clock
case beyond the settle-margin contract. The `strands_evals` suite does not
collect in this environment (module absent) and was not run.
