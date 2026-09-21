# The convention — what exists, written down

Written LAST, on purpose. Attempted first on 2026-09-17 (PR #307) and withdrawn
the same day: a convention written before the code describes the variations that
already exist and legitimises them.

Everything below is true of the tree as of 2026-09-17 and is held by a test.
Where something is NOT yet true, §6 says so rather than aspiring.

## 1. The rule

> **Different evaluation suites. Different input. One Nora.**

## 2. How to add a suite

**Write a corpus file. That is the whole procedure.**

```yaml
# evals/corpora/<name>.yaml
config:                       # optional — what this SUITE needs turned on
  engagement.trigger.text: "nora.000"
policy: |                     # optional — the room set, when the default won't do
  default = "deny"
  [[conversations]]
  platform = "signal"
  id = "g1"
  persons = "*"
cases:
  - name: real/weather
    entry: gate               # which entry point the input enters at
    expect: go
    sample: 5
    knows:                    # what she KNOWS when the case starts
      transcript: ["今日好似好凍", "深水埗而家幾多度？"]
```

Then `python -m evals.rebuilt evals/corpora/<name>.yaml`.

No module, no driver, no registration. `tests/architecture/
test_the_entry_point_is_a_port.py::test_adding_a_suite_adds_no_code` is what
keeps that true — it holds that the runner reaches an entry point only through
`ENTRY_POINTS`, so there is no name a new suite could need it to import.

## 3. The four things

> **CORRECTED 2026-09-18 — three of these four rows point at code the SDK
> already ships.** `strands_evals.Case`, `Experiment.run_evaluations` and
> `EvaluationData`/`EvaluationOutput` replace `domain.py` and `runner.py`;
> only **Subject** is ours. See `07-sdk-native` §2 and `01-boundaries` §2,
> which carries the same correction on the same table. This file is cited as
> normative from `Makefile`, so the uncorrected version of this table is the
> single most misleading thing in the bundle.


| | what it is | where |
|---|---|---|
| **Case** | input + expectation + entry point | `evals/rebuilt/domain.py` — pure, imports nothing from `nora` |
| **Subject** | Nora. Obtained, never built | `nora/subject/obtain.py` — one function |
| **Runner** | case in, outcome out | `evals/rebuilt/runner.py` — one function, no suite branch |
| **Outcome** | what happened, one shape | `evals/rebuilt/domain.py` |

## 4. The four rules, and the test that holds each

| rule | held by |
|---|---|
| **The eval layer never composes a subject.** | `test_the_eval_layer_composes_nothing` — AST, refuses a call to any of the seven assemblers |
| **Adding a suite adds no code.** | `test_adding_a_suite_adds_no_code` |
| **`nora.app` never learns it is being measured.** | `test_the_subject_is_obtained_not_assembled.py` — AST walk over `nora/app/**` |
| **One way to compose.** | `test_obtain_is_the_only_thing_that_composes` — every public name that returns a `Subject` |

All four were falsified before being trusted: each was attacked, each went red,
each was restored. A pin that has never failed is a pin nobody has checked.

## 5. The rules that are not tests, and why they still bind

### An input the graph cannot honour is an ERROR

Not a FAIL, and never a PASS. `Outcome.meets` returns `True`, `False` or
**`None`**, and the third answer is the whole discipline.

> **CORRECTED 2026-09-18 — the SDK has no third state, and a grader cannot
> restore it.** `EvaluationOutput.test_pass` is a `bool`, so `REVIEW` must ride
> in `label` and no aggregation may read `test_pass` alone. Worse:
> `Experiment._worker` and `_run_evaluator` write `test_pass=False, score=0` on
> any raise **without calling any evaluator**, and `EvaluationReport` has no
> error channel. So this rule has to be held by the task never raising plus a
> convention the enforcement gate reads — not by a grader.
> `08-the-sdk-surface` §4.3. Three places produce
it today:

- **Honcho `conclusions:`** — Honcho DERIVES a conclusion from messages, so
  there is no way to state one without substituting the client. Two
  participation cases.
- **multi-room cases** — a proactive case names two rooms and keys its standing
  per room; `Knowledge` is single-room. Twenty-one proactive cases. (`now:` was
  on this list until the clock seam landed — `build_dependencies(cfg, clock=…)`
  — and is now an ordinary case field.)
- **any unreachable model** — on all four entry points, each through its own
  signal (§5.1).

The alternative in every case is a suite that scores an un-run case. For the
`silence`-expecting half of a corpus that is not a near miss: **every one of
them passes**, and nothing reports it.

### 5.1 Each entry point fails closed through its OWN signal

There is no shared one, because the deployment does not offer one:

| entry point | the signal | why nothing else works |
|---|---|---|
| `gate` | `GateVerdict.source == "fail_closed"` | the gate never raises; a timeout, a refusal and an unparseable body all arrive as `no_go` |
| `participation` | `speak`'s token is not `sent`/`silent` | a failed turn writes no row, which reads as silence |
| `tag` | `nora_silent_failures_total` rose | `Dispatcher.on_message` returns `None` and spawns the turn — there is nothing else to read |
| `dreamer` | `status == "unavailable"` OR `nora_dream_sample_total{status="unavailable"}` rose | an un-run pass has an empty diff, byte-identical to a correct `none` — and `status` ALONE was not enough: a pass that asked the model and failed still answers `nothing_found` |

Every one of those four was found by RUNNING the entry point with no provider
and reading what came back. Three of the four returned a verdict.

### A number without its subject is not a number

`subject=` is printed on the report's last line (#310). There is one subject
now, and naming it is what keeps that true: the moment a second appears, that
line is where it shows up.

## 5.2 A reply is not correct for having happened

Structural grading answers *did she reply*. On the adversarial corpora that is
close to vacuous: all 28 cases expect `reply`, so `injection_dan` scores a PASS
on the literal string the attack is trying to produce.

`--judge` closes it. The rubric and the verdict logic moved into
`evals/rebuilt/` (one definition; the old files re-export until Step 5), and
`Outcome.judged` reaches `meets()` with one rule:

> **The judge can only ever REMOVE a pass, never grant one.**

- `FAIL` → the case fails, whatever the structure says.
- `REVIEW` → **ungradable**, not a pass and not a failure. It is the judge
  declining to answer, which is an error about the instrument.
- `PASS` → changes nothing. A case expecting silence that got a reply still
  fails, however good the reply was; otherwise the judge could overrule the
  corpus's own label.

Both directions are pinned and both were falsified
(`tests/unit/test_the_judge_can_remove_a_pass.py`).

Three properties it does not get to lose: the judge is **a different model**
from the one under test, and the CLI REFUSES a run where they match rather than
warning; it grades the **SAFETY criterion**, the one meaningful with or without
source data, because binding all six made a good doctrine false-fail on judge
noise; and it takes **three samples** with a majority verdict, because the same
judge caught a fabrication 1/3 on one run and 3/3 on another.

The report prints `judge=` beside `subject=`, and its loudest value is
`none — replies scored on having happened`.

## 6. What is NOT true yet

> **READ `07-sdk-native` FIRST.** This section describes a convention built
> without reference to `strands-agents-evals`, which ships three of the four
> primitives `01-boundaries` §2 derives. The costs recorded below are real but
> one of them — the routing tier — is not permanent, and the correction is
> inline where it is claimed.

- **Step 5 is DONE.** `nora/app/eval_support.py`, `nora/app/offline.py` and
  every old `evals/*.py` are gone from where they were. `evals/` is now
  `__init__.py`, `rebuilt/` and the corpora.

  Three of them were **re-homed into `tests/`, not deleted**, and that turned
  out to be what "not an eval module" actually meant:

  - **`offline.py` → `tests/offline_pipeline.py`.** Its own docstring said why
    it was in `nora/app/`: `tests/integration/harness.py` needed this wiring
    and so did `evals/harness.py`, and *"`evals/` may import `nora.app` and
    `nora.testing` only"* — so a shared TEST helper had to live inside the
    application to be reachable from both.
  - **`eval_support.py` → `tests/offline_eval_support.py`.** Same reason, said
    the same way: *"the gate's construction lives HERE rather than in
    `evals/relevance_gate.py`."* It was the boundary adapter.
  - **`evals/harness.py` + `evaluators.py` → `tests/`.** The offline driver and
    its graders. Two unit tests need them, one of which is R-97's only named
    test.

  **The import contract that forced all of them into `nora/` binds `evals/`
  alone.** With `evals/` no longer having a harness, every consumer is under
  `tests/` — which has no contract, because a test is allowed to know how the
  thing is built. So the workaround is unnecessary and they are where they
  belong.

  What this does NOT undo: `00-overview` §1's finding stands. Five of the seven
  places that build a Nora are still in `tests/offline_eval_support.py`,
  serving two unit tests that need a SCRIPTED model — which the rebuilt layer
  cannot provide. What changed is that they are no longer inside `nora/`, where
  they could be mistaken for something the deployment runs.

  **`tests/integration/test_live_parity_gate.py` is gone**, and that is the
  one-way door this bundle warned about: parity cannot outlive one of its two
  arms. The gate arm was compared once (`05-live-parity`) and found a real
  defect; the other four entry points never were, and now never can be against
  the old layer.

- **The ROUTING tier is gone with no counterpart**, and two decisions are now
  marked NOT ENFORCED in `design/09-decision-index.md` because of it —
  **DEC-76** (an intent graph grading which tool she reached for) and
  **DEC-84** (whose re-runnable gate was `--suite journeys`). `Outcome` carries
  no tool calls and no corpus is a journey.

  This is the sharpest thing the delete cost, and it is recorded rather than
  absorbed: a wrong route answers fluently from the wrong source, and DEC-75
  and DEC-76 both record that a reply-grader is blind to it. `docs/eval-sop.md`
  step 2 now says the step has no instrument.

  **CORRECTED 2026-09-18 — "no counterpart" is FALSE.** `strands-agents-evals`,
  pinned at `pyproject.toml:47` throughout, ships four instruments for exactly
  this: `ToolCalled` (deterministic), `ToolSelectionAccuracyEvaluator`,
  `ToolParameterAccuracyEvaluator` and `TrajectoryEvaluator` (LLM-judged), all
  reading `Case.expected_trajectory`. The tier is recoverable and DEC-76 and
  DEC-84 can be re-enforced. What was missing was never the instrument; it was
  that no node in this bundle had looked at the dependency. See
  `07-sdk-native` §4.1.

- **Four corpora are ungradable** — `identity_grounding`, `source_scope`,
  `injections`, `being_contradicted` — because they are about what the reply
  SAYS and the judge is too slow to run (§8.4 of `06-first-full-run`:
  ~211s a sample, five timeouts in six cases).
- **Four assertions in the note-candidates corpus are parked**, not graded:
  `must_cite`, `anchor_date`, `creates`, `updates`. They need the staged
  candidate's `message_ids`, which the notebook diff does not carry.
- **Twenty-one proactive cases and two participation cases do not run.**
  §5 says why, and both reasons are missing seams in `nora.app`, not in this
  layer.

> **RETRACTED 2026-09-18 — no case is refused any more, and this claim outlived
> its cause by two commits.** Both seams were closed: `rooms` and `standing`
> became seedable (GOAL Step 1) and `conclude()` turned out to be production's
> own write lane (`177301b4`, *"Honcho conclusions CAN be written — I asserted
> otherwise"*; `nora/subject/knowledge.py` now says *"THIS WAS WRONGLY DECLARED
> IMPOSSIBLE"*). `06-first-full-run` §8 grades `proactive_act 7/11` and
> `proactive_decide 9/11` — **22 proactive cases ran**. The two participation
> ERRORs are an unreachable Honcho SERVICE, not a missing seam.
>
> MEASURED 2026-09-18: 11 cases still carry `metadata.row`, all in
> `proactive_act.yaml` — but the `unseeded` guard lives in the **`dreamer`**
> entry point and those cases enter at `proactive_act`. **The guard cannot fire
> for any case in the tree.** A fail-closed guard whose condition cannot occur
> is not a guard; it is the shape this bundle keeps finding one layer down.


## 7. The seams `nora.app` does not have

Four found by building this, all the same shape — **the deployed graph could
not be asked for something it does**, so each driver built its own:

| | found by | closed? |
|---|---|---|
| `RelevanceGate` not on `Dependencies` | trying to reach the gate | **yes** — a field |
| `FirePath` not on `Dependencies` | trying to reach the turn | **yes** — a field |
| no clock seam | the proactive corpora | **yes** — one `clock` parameter replacing nine wall-clock reads (7 lambdas, 2 values) |
| no multi-room seeding | the proactive corpora, one layer in | **no** — found by the clock fix unblocking them |
| no Honcho write seam | two participation cases | **no** — may not be fixable |

The first two cost one line each and no behaviour change. That they were
missing at all is most of the mechanism behind seven assemblies: building a
second `RelevanceGate` was genuinely easier than reaching the deployed one.
