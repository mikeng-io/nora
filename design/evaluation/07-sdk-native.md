# The SDK was there the whole time — `strands-agents-evals`, measured

> **Read this before `01-boundaries` §2.** That table is the premise this node
> refutes, and everything from `02-the-rebuild` onward is built on it.

## 1. The measurement that opens this

```
grep -rniE "strands.?evals|strands-agents-evals|Experiment\(|EvaluationOutput" design/evaluation/
→ 0 hits across 1,601 lines
```

The bundle never mentions the evaluation SDK. Not to adopt it, not to reject
it, not to record why it would not fit. **The decision was never taken**, and
that is a different failure from taking it wrongly: there is no reasoning to
attack, so nothing in seven nodes could have caught it.

`pyproject.toml:47` has carried `evals = ["strands-agents-evals==1.0.3"]` the
whole time. Both layers' only contact with it was two classes in the OLD
`evals/judge.py` — `NoraOutputEvaluator` and `NoraTrajectoryEvaluator` —
defined, never instantiated, never called. Measured: `git grep` over
`origin/master` returns four hits, all inside their own definition. Their own
docstring said so:

> *"Neither class is invoked with a live judge model in this session ... they
> are exercised here only for importability/structure."*

So the operator's locked decision — *evaluation uses Strands-native
`strands-agents-evals`* — was never implemented by the old layer either. The
rebuild did not drift from it. It deleted the last decoration that made the
drift invisible, which is an improvement in honesty and a regression in
everything else.

## 2. What the SDK actually is — four primitives

Read 2026-09-18 off the installed `strands-agents-evals==1.0.3` and the
official docs (`quickstart`, `eval-sop`, `evaluators/custom_evaluator`,
`evaluators/deterministic_evaluators`, `how-to/eval_task`).

```python
from strands_evals import eval_task, Case, Experiment
from strands_evals.evaluators import OutputEvaluator

@eval_task()                                    # (2) SUBJECT
def task(case): ...                             #     returns Agent | str | dict

cases = [Case(name=..., input=..., expected_output=..., metadata={...})]   # (1) CORPUS
evaluators = [OutputEvaluator(rubric="...")]                               # (3) GRADING
report = Experiment(cases=cases, evaluators=evaluators).run_evaluations(task)  # (4) HARNESS
report.run_display()
experiment.to_file("...")
```

Against `01-boundaries` §2's "four things, and nothing else":

| §2's four things | what this bundle built | what the SDK already ships |
|---|---|---|
| **Case** | `evals/rebuilt/domain.py` + a bespoke YAML schema | `strands_evals.Case` — `input`, `expected_output`, `expected_trajectory`, `expected_environment_state`, `expected_interactions`, `metadata`, `session_id` |
| **Subject** | `evals/rebuilt/entry.py`, 512 lines | **nothing — this one is ours**, and it is the bundle's real finding |
| **Runner** | `evals/rebuilt/runner.py`, 530 lines | `Experiment.run_evaluations` / `run_evaluations_async` |
| **Outcome** | `domain.Outcome` + `report.py`, 507 lines | `EvaluationData` / `EvaluationOutput` / `EvaluationReport` |

**Three of the four were re-derived from first principles against a dependency
that already defines them.** 1,037 of the new layer's 2,268 lines are the
harness the SDK ships.

## 3. The three reasons this was thought impossible — two are false

Each was load-bearing and none was tested before this node.

### 3.1 "Our subject is not a Strands `Agent`" — FALSE

A task function may return a **`dict`** with at least an `"output"` key, and
optionally `"trajectory"`. `how-to/eval_task` states the five accepted shapes;
the SDK has a published framework-agnostic story covering Claude Agents SDK,
OpenAI Agents and Google ADK. A whole composed Nora graph is an ordinary case
of this, not an exception to it.

### 3.2 "The SDK requires Amazon Bedrock" — FALSE

Bedrock is the *default* judge, not the requirement. Every judge-based
evaluator takes `model: strands.models.model.Model | str`, and
`strands.models.openai.OpenAIModel` is what **production itself** already uses
(`nora/agent/factory.py:128`), constructed from an `AsyncOpenAI` client. The
same seam points at OmniRoute's `nora-judge`. No new infrastructure, and the
judge stays a different model from the subject, which `01-boundaries` §5
requires.

### 3.3 "`EvaluationOutput` cannot carry the confidence rubric" — HALF TRUE, and it is the only real constraint

The narrow claim checks out. Measured:

```
EvaluationOutput fields: ['score', 'test_pass', 'reason', 'label']
```

No `criterion`, no `confidence`. But the inference drawn from it was wrong
twice over:

- **`evaluate()` returns `list[EvaluationOutput]`**, so per-criterion scoring
  fits directly: one entry per criterion, `label` naming it.
- **Confidence is an input to the verdict, not an output of it.** The locked
  rule — `score<=0.5 & conf>=0.7 → FAIL`, `score>=0.7 & conf>=0.7 → PASS` —
  runs inside the subclass; only its result needs a field.
- `Evaluator.__abstractmethods__` is `None`. Subclassing is unconstrained.

**What IS a real constraint: `test_pass` is a `bool` and there is no third
state.** `REVIEW` — the judge that did not answer — has nowhere to go, and
collapsing it into `test_pass=False` is exactly the hole the GOAL's rule 3
exists to prevent (*ERROR is not FAIL and is never PASS*). So `REVIEW` must
ride in `label` and every aggregation must read `label`, never `test_pass`
alone. That is the one place custom code is not optional, and it is small.

## 4. Where the thirteen corpora land

| what a corpus asserts today | the SDK's own instrument |
|---|---|
| `expect: reply` on the 23 ungradable cases | `FaithfulnessEvaluator` (grounding), `RefusalEvaluator` + `HarmfulnessEvaluator` (injections), `CorrectnessEvaluator`, `InstructionFollowingEvaluator`, `StereotypingEvaluator` |
| `must_cite` as substrings | `Contains(value=..., case_sensitive=False)` |
| `admitted` go / no_go | `Equals()` |
| `creates` / `updates` / `anchor_date` — writes to the database | **`StateEquals(name=..., value=...)`**, the SDK's answer to a tool-using agent's side effects, against `Case.expected_environment_state` |
| the ROUTING tier | `ToolCalled` (deterministic), `ToolSelectionAccuracyEvaluator`, `ToolParameterAccuracyEvaluator`, `TrajectoryEvaluator` (LLM) |
| the confidence-weighted rubric | one `Evaluator` subclass |

### 4.1 The routing tier is recoverable, and two documents say otherwise

`04-the-convention` §6 and `GOAL.md` Step 8 both record the routing tier as a
cost that *"neither is recoverable by trying harder"*, leaving **DEC-76** and
**DEC-84** NOT ENFORCED. That is false. The SDK ships **four** instruments for
it, in the version already pinned. Both statements are corrected in place.

Live parity, the other cost named there, is genuinely gone — that one stands.

### 4.2 The tool-result framing has an instrument too

`GOAL.md` Step 5 — *"`[search result]` lines are not room speech"* — asked for a
decision and never got one. **No decision is recorded anywhere in this bundle**,
and the commit reading `Step 5 done` refers to `02-the-rebuild`'s own step 5,
the delete. Two different step fives, one name.

Measured 2026-09-18: `grep -niE "tool_result|tool_call_log" evals/rebuilt/*.py`
returns nothing. Every `knows.transcript` row becomes a room message, so a case
like `identity_grounding`'s

```yaml
- body: '{"windowDays": 30, "players": [{"player": "duskrunner7", "deaths": 52}, ...]}'
  is_nora: true
```

asks the subject to read a JSON blob **she said out loud in the room**, where
production carries the same bytes as a tool result. Byte-faithful, not
meaning-faithful, and it is the framing the model sees that decides the answer.

`ToolSimulator` is the SDK's instrument for this: tools registered by decorator,
responses generated by an LLM against a Pydantic output schema, state shared
across related calls — *"when real tools require live infrastructure, when you
need controllable behavior for evaluation."* It models a tool result AS a tool
result, which is the thing the corpora currently cannot say.

## 5. What we also never used, and wrote by hand instead

- `Experiment.to_file()` / `from_file()` — cases serialize as JSON; the bespoke
  YAML schema and its loader are ours to maintain for no gain.
- `LocalFileTaskResultStore` and result caching — aimed squarely at
  `06-first-full-run` §8.4, where a judged run costs ~211s per sample.
- The **red-teaming** module — five OWASP-aligned risk categories, generated
  adversarial cases, published attack strategies, breach scoring over the full
  conversation. We hand-wrote five injection cases instead. **But it is
  `experimental` and takes an `agent_factory: () -> Agent`, not a task
  function, so whether Nora can be driven through it is OPEN** — see
  `08-the-sdk-surface` §2.7. Do not retire the injection corpora on this line.
- `ActorSimulator` — multi-turn simulated actors driving a real conversation,
  goal-tracked, adapting to what she says. Every corpus here is single-turn
  scripted text, which the SDK's own documentation names as the limitation
  simulators exist to remove: *"cannot test multi-turn conversation flow, miss
  context-dependent behaviours."* The `participation` corpus — whether she
  should join an ongoing exchange — is a multi-turn question asked in
  single-turn form. `ActorSimulator` also takes adversarial profiles, which is
  what the five injection corpora are approximating by hand.
- `ToolSimulator` — see §4.2.
- `ExperimentGenerator` — cases generated from a context description.
- `detectors` — failure detection and root-cause analysis over traces.
- A CLI.
- The official **Eval SOP** (`strands-agents-sops`): a four-phase Plan → Data →
  Eval → Report agent workflow, distributed as an MCP server and a Claude Code
  skill. **`docs/eval-sop.md` in this repo is a same-named, independently
  written document** and is not that SOP.

## 6. Enforcement — three layers, weakest first

The locked decision was never enforceable, which is most of why it decayed
silently. Naming it again without a gate would repeat that.

1. **Dependency.** `strands-agents-evals` sits in
   `[project.optional-dependencies]`. Not installing it is currently a
   supported state, so the suite is allowed to exist without it. Move it.
2. **Import contract.** `lint-imports` expresses *"may not import X"* and has
   no *"must import X"*. This layer cannot carry the rule, and
   `01-boundaries` §6.1 already measured that the import contract is the
   looser of the two gates.
3. **Architecture test** — the binding gate, under `tests/architecture/`, by
   AST over `evals/`:
   - no module defines its own experiment runner or report renderer;
   - every grader is a subclass of
     `strands_evals.evaluators.evaluator.Evaluator`;
   - a suite is still `Case` data and adding one still requires no code
     (`01-boundaries` §2's own test, which survives unchanged).

   Falsify each before trusting it, per this bundle's rule 2.

## 7. What survives, and it is not nothing

`00-overview` §1/§2 and `03-step0-results` measured **production's own
composition** — FirePath built at 21 call sites where the deployment builds 36,
seven places that assemble a Nora and none of them `build_dependencies`. That
finding is orthogonal to which harness grades the result, it is why
`nora/subject/obtain(spec)` exists, and it is untouched by this node.

`05-live-parity`'s speaker-rotation defect — one corpus syntax meaning two
different things in two drivers, found only by running both — also stands.

**So the rebuild produced one thing worth keeping and one thing to discard**,
and they are cleanly separable: the subject is ours, the harness is the SDK's.

## 8. The reading that would refute this node

Drive one corpus — `identity_grounding`, eight cases — end to end through
`Case` + `@eval_task` + a custom confidence `Evaluator` + `OutputEvaluator`,
with the judge on OmniRoute's `nora-judge`, and compare against
`06-first-full-run` §8's structural-only number.

**If the subject cannot be reached from a task function**, or the confidence
rubric cannot be expressed in a `list[EvaluationOutput]` with `REVIEW` intact,
this node is wrong and §3 is where it is wrong. Nothing here has been run yet;
§2–§6 are read off the installed package and the published documentation, and
that is a weaker grade of evidence than this bundle's measured nodes carry.
