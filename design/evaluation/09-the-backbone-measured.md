# The backbone, measured — a populated Session, and an ERROR that stayed an ERROR

`GOAL-2-backbone.md` Steps 0 and 1, run 2026-09-18 against the deployed `nora`
alias over an `ssh -L 20128` tunnel, on a `pgvector/pgvector:pg18` container
migrated per `tests/conftest.py`.

**This node is measurement.** Everything in `07-sdk-native` and
`08-the-sdk-surface` was read off a package; this was run.

## 1. Step 0 — the Session is populated. `08` §1 holds.

The question that decided the plan: does a `Session` with real content reach a
`TRACE_LEVEL` evaluator, for an Agent-backed entry point?

**Yes.** Two `participation` cases, driven through `Case` +
`@eval_task(TracedHandler())` + `run_evaluations_async(max_workers=1)`:

```
case p1 — traces: 1, spans: 6
  InferenceSpan       messages=5
  ToolExecutionSpan
  InferenceSpan       messages=7
  ToolExecutionSpan
  InferenceSpan       messages=9
  AgentInvocationSpan

case p2 — traces: 1, spans: 4   (InferenceSpan ×2, ToolExecutionSpan, AgentInvocationSpan)
```

The messages are real: her `<context_header>`, the rendered time block, the
`<tag_message>`, her `ToolCallContent(name='send_reply', arguments={...})`, and
the `ToolResultContent(content='refused')` that came back.

**Three things follow, and the third was not expected:**

1. `AgentInvocationSpan` is present, which is what `Evaluator._get_last_turn`
   needs. The ten `TRACE_LEVEL` evaluators are reachable for the 110
   Agent-backed cases.
2. **`ToolExecutionSpan` is present.** The ROUTING tier is not merely
   recoverable in principle (`07-sdk-native` §4.1) — the spans it needs are
   already in the trace. `ToolCalled`, `ToolSelectionAccuracyEvaluator` and
   `ToolParameterAccuracyEvaluator` have real material.
3. The retry loop is visible in the span list: `send_reply` was refused, so she
   called it again, three inference spans for one turn. A trajectory evaluator
   would see that; nothing in the old layer could.

`08-the-sdk-surface` §4.2's other half stands untouched: the GATE emits no
mappable spans, and its 40 cases remain broken.

**STATUS UPDATE, 2026-09-19 — no longer true; see `08-the-sdk-surface` §4.2's
own status note.** An unrelated tracing-replacement session (adopting
`strands.telemetry` in place of the hand-rolled `nora.obs.tracing.
generation()` this bundle was written against) moved the gate's raw call
onto `nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`,
which DOES emit mappable `gen_ai.system.message`/`.user.message`/`.choice`
span events — the same shape `participation`'s `AgentInvocationSpan`/
`InferenceSpan` case above is read against. Whether the gate's 40 cases
actually score correctly now is **unmeasured**: nobody has re-run this
node's own Step 0 probe against `relevance_gate` the way it was run against
`participation` above. That re-run — not a repeat of this node's existing
measurement — is the next falsification a backbone session should do before
assuming the gap is closed.

## 2. What the first attempt got wrong, and it is the node's own subject

**The task's `metadata` is silently dropped.** The first probe returned the
failure reason on a `metadata` key and the report showed `metadata: {}`.
MEASURED: `Experiment._run_task_async` reads exactly five keys — `output`,
`trajectory`, `interactions`, `environment_state`, `input` — and the
`metadata` on the resulting `EvaluationData` is the **case's**, never the
task's.

So the first run reported an empty `Session` AND an empty output, and the
reason — `could not obtain the subject: No module named 'psycopg2'`, a DSN
scheme mistake of mine — was invisible. **An instrument fault presented as a
measurement of Nora**, in the probe written to look for exactly that. It was
found only by printing the `Outcome` to stderr, outside the SDK entirely.

`environment_state` is the channel that survives, and the spine uses it.

## 3. Step 1 — the spine, and the ERROR that stayed an ERROR

`evals/backbone.py`. Layers 3 and 4 only: no runner, no report renderer, no
outcome type. Three hand-written `Case` objects, one of them pointed at a model
alias that does not exist.

```
speak-1      label='FAIL'    expected a reply and said nothing
silence-1    label='PASS'    said nothing, as the case expects
error-1      label='ERROR'   the case did not run: the turn did not run: failed_error

READING:       1/2 graded, 1 ERROR
overall_score: 0.3333...        <- the SDK's own number, for contrast
```

**The contrast is the result.** `error-1` expects silence and produced silence,
so every built-in evaluator would score it a PASS — the *"for the
silence-expecting half of a corpus EVERY ONE PASSES"* failure, reproduced on
demand. The absence grader reads the fail-closed token off `environment_state`
and calls it ERROR. And `overall_score` folds the broken instrument into the
denominator to make 0.33, while the reading says **1 of 2 gradable**.

### 3.1 Two runs, and the gradable half flipped

The spine was run twice, the second time only to re-verify after a constant was
renamed. The two model-dependent cases **swapped verdicts**:

| | run 1 | run 2 |
|---|---|---|
| `speak-1` | FAIL — said nothing | **PASS** — «「邊間茶記」——你指邊間先？» |
| `silence-1` | PASS — said nothing | **FAIL** — wrote 3 rows |
| `error-1` | ERROR | ERROR |
| **reading** | 1/2 graded, 1 ERROR | 1/2 graded, 1 ERROR |

**The only stable verdict was the ERROR.** At `sample: 1` the gradable half of
a three-case run is a coin toss, and the aggregate was identical both times
while every underlying case moved — which is exactly the shape that makes an
aggregate readable and a case verdict not.

This is `domain.py`'s own rule (*"one draw of a stochastic process is not a
reading of it"*) reproduced on three hand-written cases, and it lands straight
on `08-the-sdk-surface` §4.4: **`Experiment` has no repeat parameter**, and the
workaround — five `Case`s per case — breaks both `overall_score`'s denominator
and `EvaluationDataStore`'s unique-name requirement. Sampling is now the
sharpest open problem in the port, ahead of the gate's spans.

### 3.2 `label` survives in exactly one place

MEASURED: `EvaluationReport` carries `overall_score`, `scores`, `cases`,
`test_passes`, `reasons`, `detailed_results`, `diagnoses`, `recommendations`.
**`label` is on none of them except `detailed_results`**, a
`list[list[EvaluationOutput]]`. It is not on `report.cases` — those rows are
the `EvaluationData` — and it is not derivable from `scores` or `test_passes`.

So the two fields a reader reaches for first are precisely the two that cannot
distinguish a broken instrument from a wrong answer. `backbone.read()` exists
for that reason and keys on `detailed_results`.

## 4. The async escape, taken and stated

`@eval_task`'s wrapper is sync and never awaits; every entry point is
`async def`. Two escapes (`08` §4.6). **Taken: `asyncio.run` inside a sync
function**, because the other one — passing a bare `async def` — bypasses
`eval_task` and therefore `TracedHandler`, which is the only thing that
produces a `Session`.

The cost is `max_workers=1`. That is not a concession: `TracedHandler` shares
one span exporter and is documented serial-only, so the constraint arrives
twice and the second one is not ours to relax. **Whether the suite is fast
enough serially is now the open operational question**, and it is the
operator's, not this node's.

## 5. What this leaves behind

- **`room_for`, `workspace_for`, `_knowledge` and `_close` are layer 3 living
  in `runner.py`, which is layer 1 and is to be deleted.** `backbone.py`
  imports them with a comment saying so. They must move before the delete, and
  that import is the marker.
- The two model-dependent cases are not stable at one draw (§3.1). A bare
  subject has neither memory nor group context — in the Step 0 run she said so
  herself: *"查唔到嘢——memory 同 group 都 read 唔到"* — so what these two cases
  measure is under-specified. That is a case-construction question, not a spine
  one. **The spine reported both runs correctly, which is what was under
  test.**
- The `send_reply` guard refuses with no platform daemon in front, producing
  the retry loop in §1. Worth deciding, not worth blocking on.

## 6. The reading that would refute this node

Run the same three cases with `max_workers=10`. If the `Session`s do not bleed
across cases, `08` §2.2's serial-only constraint is softer than documented and
the throughput question changes. If they do bleed, that is the measurement that
closes it.
