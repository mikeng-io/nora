# The SDK surface, subsystem by subsystem — what exists, and what of ours it replaces

`07-sdk-native` establishes that the SDK was never considered. This node is the
inventory: what `strands-agents-evals` actually contains, read off the
**installed 1.0.3** and the published documentation, and what each piece
replaces in what this repo hand-wrote.

**Provenance.** Every "present" below is a probe of the installed package on
2026-09-18, not a docs claim — the docs describe 1.3.0 and we pin 1.0.3. Read
docs fully: `quickstart`, `eval-sop`, `evaluators` (overview),
`evaluators/custom_evaluator`, `evaluators/deterministic_evaluators`,
`how-to/eval_task`, `how-to/result_caching`, `how-to/experiment_management`,
`simulators` (overview), `detectors` (overview), `red-teaming` (overview),
`experiment_generator`, `cli`. The individual evaluator pages were read at
signature level only.

## 1. The organizing concept this bundle does not have: evaluation LEVEL

| level | scope | what it needs as input |
|---|---|---|
| `OUTPUT_LEVEL` | one response | a string |
| `TRACE_LEVEL` | one turn | a **`Session`** — OTel spans, mapped |
| `SESSION_LEVEL` | the whole conversation | a **`Session`** |

**This is the finding that reorganises everything else.** Our `Outcome` carries
a reply, some database rows and nothing else, so it can only ever feed
`OUTPUT_LEVEL`. And `OUTPUT_LEVEL` has exactly two members —
`OutputEvaluator` and the multimodal one.

Every evaluator that would close the 23 ungradable cases —
`FaithfulnessEvaluator`, `CorrectnessEvaluator`, `RefusalEvaluator`,
`HarmfulnessEvaluator`, `InstructionFollowingEvaluator`,
`StereotypingEvaluator`, `CoherenceEvaluator` — is **`TRACE_LEVEL`**. So is
every tool-usage evaluator. `TrajectoryEvaluator`, `InteractionsEvaluator` and
`GoalSuccessRateEvaluator` are `SESSION_LEVEL`.

**The single highest-value thing to build is therefore not a grader. It is the
span-to-`Session` seam**, and one measurement says it is reachable: Nora
already passes `trace_attributes` into the Strands `Agent` at
`nora/app/fire.py:2013`, `nora/app/agent_runner.py:181` and
`nora/agent/dreamer.py:1660`. The spans exist. What is missing is collecting
them per case (`StrandsEvalsTelemetry().setup_in_memory_exporter()`) and
mapping them (`StrandsInMemorySessionMapper.map_to_session(spans,
session_id=case.session_id)`).

`04-the-convention` §6 records the routing tier as lost. It is one seam away,
and the same seam unlocks ten other evaluators at the same time.

## 2. The seven subsystems

All probed present in 1.0.3 unless noted.

### 2.1 Core — `Case` / `Experiment` / `EvaluationReport`

Replaces `runner.py` (530), `report.py` (198) and most of `domain.py` (309).
`Experiment.run_evaluations(task)` / `run_evaluations_async(task,
max_workers=)`. `EvaluationReport.flatten([...])` merges reports;
`report.cases` rows carry an `evaluator` key. `to_file()` / `from_file()` make
an experiment a JSON document — which is what our bespoke YAML corpus schema
and its loader are for.

### 2.2 `@eval_task` — the subject seam

The one place our work survives intact. A task function may return an `Agent`,
a `str`, or a **`dict`** with at least `"output"`, optionally `"trajectory"`
and environment state. `EvalTaskHandler` subclasses give `before(case)` /
`after(case, result)` hooks — the natural home for per-case room and workspace
isolation, which `06-first-full-run` §1 identifies as the root cause of most of
the withdrawn run.

The CLI documentation states the contract most plainly: `--agent` is the
standard path and **rejects a prebuilt `Agent` instance** because conversation
state would leak across cases; `--task` is *"the escape hatch for non-standard
task shapes — multi-turn loops, custom session mapping"*, typed
`Callable[[Case], dict|str]`. **Nora is a `--task` subject, and that is a
supported first-class shape, not a workaround.**

### 2.3 Evaluators — 24 built-in plus a base class

Grouped by what they answer: response quality (7), multimodal (5), tool usage
(2), conversation flow (2), goal achievement (1), resilience (3), deterministic
(5: `Equals`, `Contains`, `StartsWith`, `ToolCalled`, `StateEquals`).

`Evaluator.__abstractmethods__` is `None`; `evaluate()` returns
`list[EvaluationOutput]`. Skill-level evaluators arrived in 1.2.0 and are
**absent** from our pin — the only gap the probe found.

### 2.4 Detectors — `detect_failures` / `analyze_root_cause` / `diagnose_session`

The subsystem with no counterpart anywhere in this repo, and the one that
answers the question this bundle keeps asking by hand. Evaluators say a case
scored 0.3; detectors say **which span failed, which of ~20 categories it
falls into, what the causal chain was, and what to change**. It wires into
`Experiment(diagnosis_config=DiagnosisConfig(trigger=DiagnosisTrigger.ON_FAILURE))`
— probed present: `Experiment.__init__` accepts `diagnosis_config`.

Operates on `Session`, so it is downstream of the same §1 seam.

### 2.5 Simulators — `ActorSimulator` / `ToolSimulator`

`ActorSimulator` drives multi-turn conversation with a goal-directed simulated
actor, adapting to what she says; profiles include adversarial actors.
`ToolSimulator` registers tools by decorator and has an LLM generate
schema-validated responses with state shared across calls.

Both bear directly on defects this bundle already recorded: the `participation`
corpus asks a multi-turn question in single-turn form, and `ToolSimulator` is
the instrument for `GOAL.md` Step 5's never-taken decision (`07-sdk-native`
§4.2).

### 2.6 `ExperimentGenerator` + `TopicPlanner`

Generates cases and rubrics from a context description, distributing across
topics for coverage. Relevant less as a case source than as a **coverage
instrument**: `06-first-full-run` §1 records that four `no_trigger` cases *"were
copies of `plain_no_mention` wearing four rule names"* and that 8 of 16 passed
with both matching rules deleted. Topic-planned generation is aimed at exactly
that failure.

### 2.7 Red teaming — `strands_evals.experimental.redteam`

Present in 1.0.3. Five risk categories mapped to the OWASP LLM Top 10
(`guideline_bypass`, `system_prompt_leak`, `harmful_content`,
`data_exfiltration`, `excessive_agency`), an `AdversarialCaseGenerator`,
several published attack strategies, and `AttackSuccessEvaluator` scoring
breaches over the full conversation and tool trace.

**Two caveats, and they are the honest limits of this section.** The module is
marked **experimental** — *"import paths, scores, and report shapes may shift
between versions"*. And `RedTeamExperiment` takes an **`agent_factory: () ->
Agent`** (or a `MultiAgentBase`), not a task function, so unlike §2.2 there is
no documented dict escape hatch. **Whether Nora can be red-teamed through this
module is OPEN and must be probed, not assumed.** Our five injection corpora
are not safe to retire on the strength of this paragraph.

### 2.8 `EvaluationDataStore` / `LocalFileTaskResultStore`

Caches task results keyed on `case.name`: run the subject once, then iterate on
evaluators without re-invoking her. Two methods to implement for a custom
backend.

This is the answer to two separate findings in `06-first-full-run`. §8.4 — the
judge costs ~211s per sample, so a judged suite is a day of wall time — becomes
one subject run plus cheap evaluator iterations. And §8.3 — forty gate calls in
7 seconds, the gateway silently replaying a cache — becomes a cache that is
**declared, keyed and inspectable** instead of one that is invisible and makes
a re-run unreadable.

## 3. What we would actually have to build

Short, and none of it is a harness.

1. **The span-to-`Session` seam** (§1). The highest-value item; unlocks 15+
   evaluators, the detectors and the routing tier at once.
2. **A confidence-weighted `Evaluator` subclass** carrying the locked rubric,
   with `REVIEW` in `label` because `test_pass` is a `bool`
   (`07-sdk-native` §3.3).
3. **An `EvalTaskHandler`** holding per-case room/workspace isolation and the
   four entry points as a selection on the case.
4. **`StateEquals` inputs** — the task returning database side effects as
   environment state, so `creates`/`updates`/`anchor_date` become SDK checks.
5. **The enforcement gate** (`07-sdk-native` §6), so this cannot decay silently
   a second time.

## 4. What does NOT fit, stated before anyone relies on it

### 4.1 Half this suite grades an ABSENCE, and the SDK has no instrument for one

Measured across the thirteen corpora, 2026-09-18:

| expectation | cases |
|---|---|
| `no_go` | 22 |
| `silence` | 21 |
| `none` | 20 |
| `no_reply` | 12 |
| **absence is the correct answer** | **75 of 150** |

**Every built-in evaluator judges `actual_output`.** The SDK's shape is *the
agent produced something, score it* — `OutputEvaluator`,
`FaithfulnessEvaluator`, `HelpfulnessEvaluator`, all of them. There is no
`SaidNothing`, and `Equals(value="")` is not one, because **a crash also
produces an empty string**. That distinction is this bundle's rule 3 (*ERROR is
not FAIL and is never PASS*) and `06-first-full-run` §1 records what it costs
when it is lost: *"for the silence-expecting half of a corpus EVERY ONE
PASSES."*

`01-boundaries` §5.1 already measured why this cannot be delegated: with no
provider reachable, **three of four entry points returned a verdict rather than
an error** — the gate fails closed to `no_go`, a failed turn writes no row and
reads as silence, `Dispatcher.on_message` returns `None`. Each needed its own
fail-closed signal *because the deployment offers no shared one*. No SDK
feature can supply it; it is knowledge about Nora, not about evaluation.

So: a custom `Evaluator` that distinguishes **correct silence** from **absence
caused by failure**, reading the fail-closed signal the task function carries.
It is small, and it is the one grader that could not have come from any SDK.

### 4.2 The rest

- **`test_pass` has no third state.** Structural, permanent, and the reason
  §3.2 is not optional.
- **Red teaming's `agent_factory`** (§2.7) — OPEN.
- **Entry points that are not agent turns** — `RelevanceGate.decide` is a
  component call, not a conversation. Whether it emits spans a mapper accepts
  is OPEN, and 40 of 150 cases ride on it.
- **Skill-level evaluators** need ≥1.2.0.
- **Live parity is still gone.** No SDK feature restores an arm that was
  deleted.

### 4.3 So: can the suite be 100% SDK?

No, and the split is clean:

| layer | whose |
|---|---|
| corpus, harness, report, caching, CLI, detectors, generators | **the SDK's, entirely** |
| grading a reply's CONTENT | **the SDK's**, via built-ins + one confidence subclass |
| the subject — obtaining Nora, five entry points, seeding what she knows, per-case isolation | **ours, irreducibly**, and the SDK names this shape supported (`--task`) |
| grading an ABSENCE (§4.1) | **ours** — 75 of 150 cases, no SDK counterpart |

The harness was never the part that needed writing. It is the part that was
written.

## 5. The reading that would refute this node

The same one `07-sdk-native` §8 names, plus its own: build the §3.1 seam for
ONE entry point and check that
`StrandsInMemorySessionMapper.map_to_session(...)` returns a `Session` a
`TRACE_LEVEL` evaluator accepts.

**If Nora's spans do not map** — if the entry points that are not agent turns
(`RelevanceGate.decide` is a component call, not a turn) produce nothing a
mapper can use — then §1 is wrong, and with it the routing tier, the detectors
and most of the evaluator catalogue. That would leave `OUTPUT_LEVEL` grading
only, which is a materially smaller claim than this node makes.

Nothing in §1–§4 has been run.
