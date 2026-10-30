# The SDK surface, subsystem by subsystem — what exists, and what integrating it actually costs

`07-sdk-native` establishes that the SDK was never considered. This node is the
inventory and the integration bill.

> **REVISED 2026-09-18 after three adversarial reviews.** The first draft of
> this node was written off the published documentation with a shallow probe,
> and most of its §3 was wrong: one item it filed as work is already shipped,
> one risk it filed as OPEN is measured FALSE, and it missed eight
> responsibilities and a whole subsystem. Every correction is in place below
> with the measurement beside it. **The basis did not move** — three reviewers
> attacked it and none broke it. What moved is the price.

**Provenance.** Every "present" is a probe of the installed
`strands-agents-evals==1.0.3` on 2026-09-18, not a docs claim — the published
docs describe 1.3.0. Where a statement comes from the docs only, it says so.

## 1. Evaluation LEVEL — the organising concept, corrected

Probed, not read:

| level | evaluators | needs |
|---|---|---|
| `TRACE_LEVEL` | **10** — Coherence, Conciseness, Correctness, Faithfulness, Harmfulness, Helpfulness, InstructionFollowing, Refusal, ResponseRelevance, Stereotyping | a **`Session`** |
| `TOOL_LEVEL` | **2** — ToolSelectionAccuracy, ToolParameterAccuracy | a **`Session`** |
| `SESSION_LEVEL` | **1** — GoalSuccessRate | a **`Session`** |
| *no level attribute* | **14** — `OutputEvaluator`, `TrajectoryEvaluator`, `InteractionsEvaluator`, the 5 multimodal, the 5 deterministic, and the `Evaluator` base | whatever they are handed |

**The first draft got this three ways wrong** and the corrections matter: there
is a fourth level, `TOOL_LEVEL`, so "every tool-usage evaluator is
`TRACE_LEVEL`" was false; `TrajectoryEvaluator` and `InteractionsEvaluator`
carry no level rather than `SESSION_LEVEL`; and `OUTPUT_LEVEL` has **no
members at all** — `OutputEvaluator` is in the fourth row.

**The conclusion survives, restated correctly: 13 of the 26 built-ins require
a `Session`, and our `Outcome` carries none.** Everything that would close the
23 content-ungradable cases is in that 13.

## 2. The eight subsystems

All probed present in 1.0.3 unless noted. `strands_evals.evaluators` exports
**27** classes: 26 built-ins plus the `Evaluator` base.

### 2.1 Core — `Case` / `Experiment` / `EvaluationReport`

Replaces `runner.py` (530), `report.py` (198) and most of `domain.py` (309) —
**1,037 of `evals/rebuilt/`'s 2,267 lines**. `to_file()` / `from_file()` make an
experiment a JSON document, which is what our bespoke YAML schema and loader
are for. `EvaluationReport.flatten([...])` merges reports.

### 2.2 `@eval_task` and `TracedHandler` — the subject seam

**`TracedHandler` IS the span-to-`Session` seam, and it is in the pin.** The
first draft of §3 filed building it as the highest-value work; it is twelve
lines already written: `before()` clears the in-memory exporter, `after()`
collects finished spans and maps them via `StrandsInMemorySessionMapper`.

Two traps in it, both measured:

- **`processed.setdefault("trajectory", session)`** — `setdefault`, not
  assignment. A task that returns its own `"trajectory"` key, which is the
  natural place to put tool assertions, **silently discards the `Session`** and
  every one of the 13 level-bearing evaluators degrades without saying so.
- **`TracedHandler` shares one exporter and is documented as serial-only**, yet
  `run_evaluations_async` defaults to **`max_workers=10`**. Concurrency here is
  not a speed knob, it is a correctness hazard that fails open (§4.3).

The task contract itself holds as claimed: `_run_task_async` reads `output`,
`trajectory`, `interactions`, `environment_state` and an overriding `input`
from a returned dict. The CLI states it plainly — `--agent` **rejects a
prebuilt `Agent`** because conversation state would leak across cases, and
`--task` is *"the escape hatch for non-standard task shapes"*, typed
`Callable[[Case], dict|str]`. **Nora is a `--task` subject, first-class.**

### 2.3 Evaluators — 26 built-in plus a base class

See §1 for the level split. `evaluate()` returns `list[EvaluationOutput]`, so
per-criterion scoring fits; `label` is free-form; a custom `aggregator` is
honoured. `Evaluator` is **not an ABC at all** — it has no
`__abstractmethods__` attribute, so subclassing is unconstrained. (The first
draft printed `__abstractmethods__ is None` as a measurement. That was a
`getattr(..., None)` default, not a reading — a fabricated probe, and the kind
this bundle exists to catch.)

Skill-level evaluators arrived in 1.2.0 per the changelog and are absent here;
that is a docs claim, not probeable from 1.0.3.

### 2.4 Detectors — `detect_failures` / `analyze_root_cause` / `diagnose_session`

No counterpart anywhere in this repo. Evaluators say a case scored 0.3;
detectors say which span failed, which of ~20 categories it is, the causal
chain, and what to change. Wires in via
`Experiment(diagnosis_config=DiagnosisConfig(trigger=DiagnosisTrigger.ON_FAILURE))`
— probed: `Experiment.__init__` accepts it. Operates on `Session`, so it is
downstream of §4.2.

### 2.5 Simulators — `ActorSimulator` / `ToolSimulator`

`ActorSimulator` drives goal-directed multi-turn conversation, adapting to what
she says, with adversarial profiles available. `ToolSimulator` registers tools
by decorator and has an LLM generate schema-validated responses with shared
state.

Both bear on defects already recorded: the `participation` corpus asks a
multi-turn question in single-turn form, and `ToolSimulator` is the instrument
for `GOAL.md` Step 5's never-taken decision (`07-sdk-native` §4.2).

### 2.6 `ExperimentGenerator` + `TopicPlanner`

Cases and rubrics from a context description, distributed across topics.
Relevant as a **coverage instrument**: four `no_trigger` cases were copies of
`plain_no_mention` wearing four rule names, and 8 of 16 passed with both
matching rules deleted (`06-first-full-run` §1, inside its WITHDRAWN banner —
cited here as a defect that was found, not as a standing number).

### 2.7 `strands_evals.chaos` — fault injection

**Missed entirely by the first draft of this inventory.** Ships
`ChaosExperiment`, `ChaosCase`, `ChaosPlugin`, `ToolEffect`, `NetworkError`,
`Timeout`, `CorruptValues`, `TruncateFields`, `RemoveFields`, plus the three
resilience evaluators (FailureCommunication, PartialCompletion,
RecoveryStrategy) that read its runs. No counterpart in this repo and no
corpus asks its question: *what does she do when a tool fails?*

### 2.8 Red teaming — `strands_evals.experimental.redteam`

Present in 1.0.3. Five OWASP-aligned risk categories, an
`AdversarialCaseGenerator`, published attack strategies, and
`AttackSuccessEvaluator` scoring breaches over the full conversation and tool
trace.

**The signature was misquoted and the correction is the answer.** Probed:

```
RedTeamExperiment.__init__(self, cases=None, *,
  agent: 'Agent | MultiAgentBase | TargetSession | None' = None,
  agent_factory: 'Callable[[], Agent | MultiAgentBase | TargetSession] | None' = None, ...)
```

Three accepted types, not two. **`TargetSession`** is a protocol with
`StrandsAgentSession` and `StrandsMultiAgentSession` shipped as concrete
implementations — precisely the seam the first draft said did not exist.

Still OPEN, and still not a reason to retire the five injection corpora: the
module is marked `experimental` (*"import paths, scores, and report shapes may
shift between versions"*), and nobody has written a `TargetSession` over Nora.
**But the probe now has a starting point, and it is `TargetSession`.**

### 2.9 `EvaluationDataStore` / `LocalFileTaskResultStore`

Caches task results keyed on `case.name`. Aimed at `06-first-full-run` §8.4 (a
judged sample costs ~211s) and §8.3 (a gateway cache that made a re-run
unreadable, because it was invisible rather than declared). **But it collides
with sampling — see §4.4.**

## 3. The integration bill

The first draft listed four items. Three reviewers found the list wrong in
both directions. This is the corrected bill; nothing here is harness.

### 3.1 Free — already shipped

`TracedHandler` (§2.2). Use it; do not build it. Respect its two traps.

### 3.2 Small, and genuinely ours

1. **A confidence-weighted `Evaluator` subclass** carrying the locked rubric,
   `REVIEW` in `label` because `test_pass` is a `bool`. **Insufficient on its
   own — see §4.3.**
2. **An absence grader** (§4.1). The one grader no SDK could have supplied.
3. **`StateEquals` inputs** for the one assertion it can express (§4.5).

### 3.3 Large, and none of it optional

Every item below is a responsibility `evals/rebuilt/runner.py` discharges today
that neither the four-item list nor the SDK covers:

- **Async everywhere.** All five entry points, `obtain()`, `know()` and
  `_close()` are `async def`, and `@eval_task`'s wrapper is **sync and never
  awaits** (§4.6). This decides the shape of everything else.
- **Clock injection** — `SubjectSpec.now` from `case.now`. The SDK has no
  notion of the subject's wall clock, and `entry.py` records that refusing it
  was the previous wrong answer.
- **Database lifecycle** — shared Postgres, migrations, per-case workspace
  derivation, policy-template filling of `{platform}`/`{room}`.
- **Room and window isolation** — `room_for(case.name)`. The gate reads the
  newest 40 rows and the Dreamer the oldest 64, so a shared room grades a case
  on other cases' messages. This was the root cause of most of the withdrawn
  first run.
- **Async teardown** — `background.cancel_all()`, `audit.aclose()`,
  `client.close()`, best-effort, must-not-raise. *150 cases at five samples is
  750 pools.* `EvalTaskHandler`'s hooks are **sync**, so this cannot live in
  them.
- **DSN scrubbing** — `_scrubbed()` strips the database URL, password included,
  from exception text. **The SDK writes `reason: f"An error occurred: {str(e)}"`
  verbatim with no hook between the raise and the report**, so an asyncpg or
  migration failure puts the password into `EvaluationReport.reasons` and into
  anything `to_file()` writes. This is a leak, not an inconvenience.
- **Compose-failure vs behaviour-failure** — *a subject that could not be built
  is not a Nora that behaved badly.* The SDK gives both the same row.
- **Judging against the LIVE doctrine** — the judge is called while the subject
  is alive, with `subject.deps.doctrine.doctrine_text`, *"not a copy read off
  disk, which is the same file only until somebody edits it mid-run."* An SDK
  `Evaluator` runs after the task returned and the subject was torn down, so
  the doctrine text must be carried out in the task result.
- **Per-entry-point config merged under case overrides**, with the recorded
  failure that a test went green while both entry points were `None`.

## 4. What does not fit

### 4.1 Half this suite grades an ABSENCE, and the SDK has no instrument for one

Measured across the thirteen corpora:

| expectation | cases |
|---|---|
| `no_go` | 22 |
| `silence` | 21 |
| `none` | 20 |
| `no_reply` | 12 |
| **absence is the correct answer** | **75 of 150** |

Every built-in judges `actual_output`. There is no `SaidNothing`, and
`Equals(value="")` is not one, because **a crash also produces an empty
string**.

`04-the-convention` §5.1 measured why this cannot be delegated: with no
provider reachable, **three of four entry points returned a verdict rather than
an error** — the gate fails closed to `no_go`, a failed turn writes no row and
reads as silence, `Dispatcher.on_message` returns `None`. Each needed its own
fail-closed signal *because the deployment offers no shared one*.

**Adversarial review reinforced this rather than breaking it**: the SDK's
default on a missing trajectory is `ValueError` → `test_pass=False, score=0`,
which is the ERROR-as-FAIL substitution this section names, shipped as library
behaviour.

### 4.2 The gate's 40 cases are measured BROKEN, not open

**STATUS UPDATE, 2026-09-19 — the fork this section names was resolved,
in the "unscreened content on the wire" direction, by an unrelated
tracing-replacement session (adopting `strands.telemetry` in place of the
hand-rolled `nora.obs.tracing.generation()` this section is written
against).** `nora/ingest/relevance_gate.py`'s raw call now opens/closes
through `nora.agent.telemetry.start_raw_model_span`/`end_raw_model_span`,
thin wrappers around Strands' own `Tracer.start_model_invoke_span`/
`end_model_invoke_span` — which DOES emit `gen_ai.system.message`/
`gen_ai.user.message`/`gen_ai.choice` span events, carrying the room's real
transcript and the operator's real instruction text unscreened (verified:
`Tracer._add_event_messages`/`_add_system_prompt_event` record them
unconditionally). The operator confirmed this tradeoff explicitly, told the
exposure this paragraph already named, choosing to accept it rather than
strip the content or add a gate-specific screen. The debrief/standing-event
pass's model calls independently moved onto a real Strands `Agent`
invocation earlier (`4448c737`), and the hand-rolled wrapper span that had
been sitting on top of that Agent invocation (producing a redundant,
overlapping trace, not fixing the mapper problem) was removed in the same
session — so debrief/standing-event should now match the "already holds"
shape §4.2 describes below for `participation`/`tag`/`dreamer`/
`proactive_act`, though this was not verified by re-running the eval
mapper against a live case.

**RESOLVED 2026-09-19 — the 40 gate cases do NOT move.**
`09-the-backbone-measured` §1.1 re-ran this section's own probe against the
REAL production span functions: the gate's trace now maps to a real
`InferenceSpan` with real messages (`mapped traces: 1`, not `0` — this
section's shape below is genuinely fixed), but `Evaluator._get_last_turn`
**still raises**, for a different reason than the one this section documents.
It no longer raises because the trace is empty; it raises because
`TraceExtractor` — at all three evaluation levels — requires an
`AgentInvocationSpan` to extract anything at all, and the gate deliberately
never produces one (`tests/architecture/test_the_gate_does_not_form_its_
verdict_through_the_sdk.py` pins exactly the design choice that makes this
so). So the case still scores `test_pass=False, score=0` via
`Experiment._run_evaluator`'s catch — same observable outcome as before the
tracing fix, for a structurally different reason. The paragraphs below are
left as originally written (the historical record of the fork, still
accurate as of when it was measured, and the mechanism it describes — no
span events at all — genuinely no longer applies); this note plus
`09-the-backbone-measured` §1.1 are the corrections.

The first draft called this OPEN. It is not.

- `nora/ingest/relevance_gate.py` — the class docstring says *"never constructs
  an agent."* `decide` → `_ask` → `self._client.complete(...)`, under
  `nora.obs.tracing.generation()`.
- `generation()` sets **attributes only**. `grep -rn "add_event" nora/` →
  **no matches, repo-wide. Nora emits zero span events.**
- The mapper reads `span.events`, drops a `chat` span whose messages are empty,
  then drops empty traces. Reproduced with Nora's exact span shape:
  `mapped traces: 0`, `Session(traces=[])`.
- `Evaluator._get_last_turn` then raises, `Experiment._run_evaluator` catches
  it, and the case scores **`test_pass=False, score=0`**.

**So 40 gate cases would score zero, silently, for an instrument fault** — the
exact fail-open this bundle exists to prevent.

**And it is wider than the gate.** Every non-Agent model call in Nora emits the
same unmappable shape: media comprehension, the standing-event pass, the
debrief stage, the judge.

**The fix is a production change with a contract problem.** Closing it means
emitting `gen_ai.system.message` / `gen_ai.user.message` / `gen_ai.choice`
events from `generation()`. Those carry message content, and `generation()`'s
own docstring requires prompt and completion to have *already been screened* by
`nora.agent.egress` — which `nora.obs` **cannot import**, because it sits below
`nora.agent` in `pyproject.toml`'s layered contract. Either unscreened model
text goes on the collector wire, or a second gate-specific mapper is written.
**Neither is small, and 40 of 150 cases wait on it.**

What **does** hold: `participation`, `tag`, `dreamer` and `proactive_act` all
construct a real Strands `Agent`. Measured against a stub model, one produced
three spans carrying `gen_ai.system.message` / `gen_ai.user.message` /
`gen_ai.choice` and mapped to a populated `Session`. **110 of 150 cases get a
real trajectory.** The tag path re-attaches trace context across its spawn, so
the background turn keeps the case's trace id.

### 4.3 ERROR collapses into FAIL below any evaluator we can write

`test_pass` having no third state is only half of it. Three SDK paths bypass
every evaluator:

- `Experiment._worker`'s `except Exception` around the task writes
  `{"test_pass": False, "score": 0, "reason": "An error occurred: …"}` **for
  every evaluator in the list, none of which is called.**
- `Experiment._run_evaluator`'s `except Exception` / `except RetryError`: same
  shape, `label` never set.
- `EvaluationReport.overall_score` averages error-zeros and wrong-answer-zeros
  in one denominator, and the report has **no error channel at all**.

`evals/rebuilt/domain.py` has the counterparts and the SDK has none:
`Verdict.gradable`, `.errors`, and `.deterministic` — *"the CONTROL: at
temperature 0 it held across 380 calls, and if it stops holding every other
number is unreadable."*

So the task must be guaranteed never to raise, the absence grader must never
raise, and the enforcement gate must recover ERROR out of `reason`/`label` by
convention. **"The task never raises" becomes a load-bearing invariant that
nothing enforces** — the exact shape this bundle keeps refusing elsewhere.

### 4.4 Sampling has no counterpart, and it is incompatible with §2.9

`sample: 5` appears on **32 cases**. *One draw of a stochastic process is not a
reading of it.*

`Experiment.__init__(cases, evaluators, diagnosis_config)` has **no repeat
parameter**. Emitting five `Case`s instead means `overall_score` becomes a mean
over draws rather than cases, `Verdict.deterministic` cannot be computed from
the report at all, and `EvaluationDataStore` **requires unique case names** —
so the cache either collapses five draws into one or forces five names that
destroy per-case aggregation. **§2.9's answer to the slow judge and the
`sample: 5` rule are mutually exclusive as stated.**

Also lost: `Outcome.elapsed_seconds`, *"the ONLY artifact that can separate a
real call from a gateway cache hit."* `EvaluationReport` carries no timing, so
the SDK's declared cache does not answer the gateway's undeclared one.

### 4.5 `StateEquals` expresses one assertion of four

`StateEquals` is strict equality over `list[EnvironmentState]` looked up by
name, against `Case.expected_environment_state`, which is **static, authored in
the corpus**.

| assertion | `StateEquals`? |
|---|---|
| `creates: N` | **Yes.** The only clean one. |
| `updates: "<title>"` | No — membership, not equality. Degenerates to the task deciding and `StateEquals` asserting `True == True`. |
| `anchor_date: N` | No — same. |
| `must_cite` (ints) | **Structurally impossible.** The expected value is the id the store assigned at seed time; `expected_environment_state` is static and there is no late binding. |
| `expect=="create" and updates` | No `StateNotEquals`, no `StateEmpty`. |

### 4.6 `@eval_task` over an async task grades a coroutine repr

`eval_task`'s wrapper is sync and never awaits. Measured:

```
RuntimeWarning: coroutine 'nora_task' was never awaited
RESULT: {'output': '<coroutine object nora_task at 0x…>', 'trajectory': "traces=[]"}
```

An `OutputEvaluator` grades that string and reports a score.
`Experiment._run_task_async` checks `iscoroutinefunction(task)` — the wrapper
is not one — so it dispatches to `asyncio.to_thread` and the warning may never
surface.

Both escapes cost. Passing a bare `async def` to `run_evaluations_async` works
(it awaits coroutine functions) but bypasses `eval_task`, hence
`TracedHandler.before/after`, hence §3.1 is no longer free. Wrapping in
`asyncio.run` inside a sync function runs ten Nora graphs on ten threads
against one Postgres under the default `max_workers=10`, which §2.2 forbids
anyway.

### 4.7 The rest

- **Live parity is still gone.** No SDK feature restores a deleted arm.
- **Skill-level evaluators** need ≥1.2.0 (docs claim).

## 5. So: can the suite be 100% SDK?

No, and the split is clean — but the middle is thicker than the first draft said.

| layer | whose |
|---|---|
| corpus, harness, report, caching, CLI, detectors, generators, simulators, chaos | **the SDK's, entirely** |
| grading a reply's CONTENT | **the SDK's**, via built-ins + one confidence subclass |
| the subject — obtaining Nora, five entry points, seeding, isolation, async, teardown, clock, DSN scrubbing, fail-closed signalling | **ours**, and larger than four items (§3.3) |
| grading an ABSENCE | **ours** — 75 of 150 cases |

**The harness was never the part that needed writing. It is the part that was
written.** That conclusion survived three adversarial reviews; the price of the
seam did not.

## 6. The reading that would refute this node

§4.2 is measured and §4.6 is reproduced, so the cheapest refutation is of the
part still standing: build the **backbone** — one Agent-backed entry point
(`participation`), one corpus, `Case` + a `TracedHandler` subclass +
`run_evaluations_async(max_workers=1)` + one built-in `TRACE_LEVEL` evaluator —
and check a populated `Session` reaches it.

**If the 110 Agent-backed cases do not map either**, §1 and §4.2's surviving
half both fall, and what is left is `OUTPUT_LEVEL`-shaped grading on a string,
which is a materially smaller claim than this node makes.


## 7. Observability migration compatibility — 2026-10-30

The accepted [SigNoz target](../../docs/observability.md#accepted-direction-self-hosted-signoz)
and [ADR 0034](../../docs/adr/0034-self-hosted-signoz-unifies-application-and-llm-observability.md)
change the trace backend, not this bundle's SDK-native runner/Session ownership.
Earlier measurements above remain evidence for the pinned SDK and configurations
that produced them; they do not prove a different emitted message format maps.

Before changing Strands' GenAI convention/content flags, replay generated
Agent-backed and raw-model spans through the existing eval extractor and read the
actual populated Session: input, output, tools and invocation boundaries. Token
and message visibility in SigNoz is a separate backend acceptance; neither result
proves the other. Preserve the existing redaction/content boundary. Do not replace
the SDK mapper with a hand-built trajectory or treat an empty Session as PASS.
Inventory Langfuse-specific result/readback consumers before retirement; changing
the trace destination does not migrate their stored results or dataset history.
The migration remains unbuilt; no eval was run by this documentation amendment.
