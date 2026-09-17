# Evaluation — one Nora, many inputs

## 0. The invariant

> **Different evaluation suites. Different input. One Nora.**

Operator, 2026-09-17. Everything below is that sentence with its consequences
written out. It is not a goal or a direction; it is the property that decides
whether any number out of this layer means anything.

## 1. Why this is a DELETE, not a refactor

`evals/` is rebuilt from zero. The directory is removed, not modified.

**Because modifying is what produced the current state.** Every driver in there
was a reasonable local change — somebody needed to measure a thing, the existing
shape did not fit, so they added a path beside it. Eight times. The result is not
a messy implementation of one idea; it is eight ideas, and no amount of editing
converges them, because each edit is again a local change that has to fit what is
already there.

The operator's version:

> 因為你改，你最終只會越改越錯。

The measurement that makes this concrete is §2: there is no single subject to
refactor **toward**. A refactor needs a target shape that the current code is an
imperfect version of. Here the current code is six different shapes, and the
target is in production.

## 2. What is actually there — six Noras

Every place an eval composes a subject, and what it wires:

```
evals/harness.py:612        build_offline_pipeline(14 kwargs)   participation, journeys
nora/app/eval_support.py:1109   DreamerRuntime(9 kwargs)        note_candidates
nora/app/eval_support.py:1359   DreamerRuntime(11 kwargs)       proactive        <- DIFFERENT
nora/app/eval_support.py:980    RelevanceGate(8 kwargs)         relevance_gate
nora/app/eval_support.py:1303   RegistryToolSource(9 kwargs)    proactive's tools
evals/run.py:665            Agent(5 kwargs)                     adversarial
```

**The two `DreamerRuntime`s are not the same object** — one takes `grants` and
`tool_budget_overrides`, the other does not. So `note_candidates` and
`proactive_decide` measure two different Dreamers, and nothing says so.

**None of the six is `build_dependencies`**, which is what the deployment runs.

### 2.1 What that means for every number this layer has produced

| reading | subject |
|---|---|
| `being_contradicted` 67% / 53%, `identity_grounding` 42% (#227) | `Agent(doctrine.text, tools=[])` |
| `proactive_decide` recall 0.20–0.50 (#158) | `DreamerRuntime(11 kwargs)` |
| `note_candidates` 1 − 0.7^N sampling | `DreamerRuntime(9 kwargs)` |
| the gate's 35/35 `no_go` (#221) | `RelevanceGate(8 kwargs)` |

Four numbers, four different objects, none of them the deployed one — and all
four have been quoted in the same breath as statements about Nora, by this
session among others. **No two of them are comparable, and none is about her.**

## 3. The chain this exists to serve

```
a design decision            DEC-n / R-n in design/09-decision-index.md
  → the behaviour it claims   what she must or must not DO
    → an evaluation that can refute it
      → driven against the one Nora
```

Measured on `design/09-decision-index.md`: **250 decision and rule rows — 10 cite
an `evals/` instrument (five of them the bare-model corpora), 196 cite a unit
test only, 44 cite nothing.** Of the 92 `R-*` rules, which are behavioural by
construction: 4 have an eval, 52 are guarded by a test that measures wiring, 36
have nothing.

**Corrected 2026-09-17, and the correction matters**: of those 52, **8 are
DEPRECATED/RETIRED/REVERSED** (mechanically counted) and roughly 30 are genuinely
settled by a mechanism — a lock, a `PRIMARY KEY`, a registry, a startup gate. The
population that needs a behavioural instrument is closer to **13** than 52. That
is a judgement from reading them, not a measurement, and it is flagged as such.

Thirteen is a list, not a substrate problem — **but the list cannot be worked
while the thirteen would be measured against six different objects.** That is why
this comes first and the list comes after.

## 4. Out of scope, said first because it bounds everything

**The test suite.** Unit, integration and e2e are solved outside this repo:

> 你 search 返到底人哋 unit test 點做、integration test 點做、e2e test 點做，就已經
> 完咗件事。嗰度其實冇乜嘢好傾。

Correct, and it bounds this bundle. The test layer's blast radius is *"do we
catch a mistake before it ships"* — it does not reach runtime behaviour. Look up
standard practice and follow it; do not invent a convention for it here.

**What has no industry answer is whether an LLM agent behaves as designed.** That
is the only thing in this bundle.

## 5. What survives the delete

Not the drivers. The things that took real work and are independent of how the
subject is assembled:

- **The corpora** — `evals/**/*.yaml`, 13 files. Cases and expectations are data
  and were never the problem. They are re-homed, not rewritten.
- **The grading** — the judge, the rubric, the structural graders. What a verdict
  means does not depend on how Nora was built.
- **The findings recorded inside them.** Corpus headers carry measured readings
  and their caveats; those are provenance and travel with the cases.
- **The `subject=` provenance token** (#310), which is the one idea from tonight
  worth keeping: the qualifier travels with the number into logs and pastes,
  where prose in a document does not.

## 6. What this bundle does NOT claim

- **That the six subjects are each wrong.** Several are locally reasonable —
  `participation_gate=None` is deliberate, documented, and right (*"a suite that
  had to get past a live gate call first would be measuring the gate's verdict
  and the turn's behaviour in one number"*). The defect is that six exist and
  nothing relates them, not that any one is badly built.
- **That the rebuild is cheap.** `evals/` is 5,629 lines of Python and 13
  corpora; `nora/app/eval_support.py` is another 1,739. §`02` sizes it.
- **A convention for writing suites.** That is written LAST, describing what
  exists. Writing it first describes the six variations and legitimises them,
  which was attempted on 2026-09-17 (PR #307) and withdrawn the same day.

## 7. The reading that would refute this bundle

If the six subjects turn out to produce **the same prompt** for the same input,
then they are one Nora wearing six coats and the delete is unjustified.

Nobody has checked. `test_assembler_parity.py` compares two call sites' kwarg
NAMES; `test_offline_pipeline_assembler.py` checks that `notes`/`standing` reach
the prompt in the offline path alone. **Nothing compares what two subjects
actually send to the model.**

That check is cheap and decisive — same input, each path, diff the assembled
prompt — and it should be run before the delete, not because the delete is in
doubt but because the diff is the specification for what the one subject has to
carry.
