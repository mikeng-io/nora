# Evaluation — a sandbox you run Nora in

## 0. The sentence

> **The eval suite is a sandbox. You build Nora, you pick which runtime you want
> to watch, you feed it input, you look at the output. Your change lives in the
> worktree. The suite does not change.**

Operator, 2026-09-17:

> 你有咩改動就寫喺個 worktree 度，你唔使改嗰個 evaluation suite 㗎嘛 … 你只不過係
> build Nora，然後你要揀返嗰個 runtime … 然後有咩 input，你咪跑囉。至於你後面嗰啲
> data store、state store、fake store，係你後面再 config 嘅啫嘛。

Four things, and the order is the design:

| | |
|---|---|
| **Nora** | built once, unchanged, not assembled here |
| **which runtime** | a selection — the note-candidates Dreamer, the participation path, the gate. Not code |
| **input** | data |
| **the stores** | config behind it. Fake, seeded, real — a knob, not part of the experiment's definition |

**The experiment is the change in your worktree.** Not a thing you add to the
suite.

## 0.1 The defect, stated at its root

Today, measuring a new capability means **writing a new driver**. Eight exist,
one per thing somebody wanted to measure. That is the disease in one line: the
suite is something you EXTEND per experiment, so every experiment gets to decide
what it is measuring, and one of them decided on a bare model.

Under §0 there is nothing to extend. A new capability is a new selection and new
input — both data — and if that requires editing the runner, the runner is wrong.

## 1. What the withdrawn attempt got wrong, and why it matters here

It wrote the same opening sentence — *build the whole Nora, change nothing, add
the thing under test* — and then listed **seams that may be substituted** and
**categories a suite may fall into**.

That is the error. Describing the eight ways the tree currently varies, and
naming them legal, is not a fix for a problem caused by variation. It
**codified the drift**. Two adversarial reviews reached the same verdict from
different directions: *right as a diagnosis, not achievable as an architecture*,
and *a rule nothing satisfies gets ignored* — zero of nine suites satisfied its
first requirement on the day it was written.

The operator's correction is the design:

> 攞返 Nora 自己本身入去，然後再加返你要 test 嘅嘢，其實件事已經完咗

So this bundle proposes no seam list, no category taxonomy, and no rule about
how to build a subject — because under it, a suite does not build one.

## 2. The defect, measured

**Eight bespoke drivers**, each composing its own subject
(`evals/run.py` unless noted):

```
_run_adversarial_live        :671      _run_suite                  :906
_run_participation_live      :983      _run_note_candidates_live  :1269
_run_proactive_decide_live  :1426      _run_proactive_act_live    :1499
_run_journeys_live          :1585      run_gate_suite    (evals/relevance_gate.py:325)
```

and **five places that compose a subject** — `evals/run.py`, `evals/harness.py`,
and three sites in `nora/app/eval_support.py`.

**The adversarial family is the one that drifted furthest**, into
`Agent(model, system_prompt=doctrine.text, tools=[])` with a plain-text message:
no `build_prefill`, no `ContextBlock`s, no `untrusted`/`note=` framing on the
message being judged, no `<standing>`/`<notes>`, no tool registry, no
`ReplyGuard`/`Deliverer`. Its numbers were reported as her behaviour for as long
as it existed.

**But the drift is the symptom.** Nothing in the tree contradicted it because
there was no shared subject to contradict it *with*. Eight drivers, no baseline,
no rule — one of them being a bare model is what that arrangement produces
eventually, not a separate mistake.

## 3. What the shape is

**One composition.** Nora is built once, through the production composition root.
A suite cannot reach the builder, so there is nothing for a suite to get wrong.

**One runner.** Given a subject, a selection and an input, it runs and reports.
No per-suite drive function, so nowhere for a suite to differ in HOW it runs.

**Everything else is config or data.**

- *which runtime* — a selection. `FirePath.speak`, `RelevanceGate.decide`, the
  note-candidates `DreamerRuntime`. This is what the eight drivers' differences
  collapse into.
- *input* — the case.
- *the stores* — config. Seeded per case, same implementation throughout.

The load-bearing claim, and the thing to attack: **every real difference between
today's eight drivers is a selection plus input plus store config.** §4 records
the two that are not obviously so.

**The test of the whole bundle:** adding a capability must require no edit to the
runner. If it does, the runner has become the ninth driver.

## 4. The two cases that decide whether this works

### 4.1 `relevance_gate` — a real component, no turn

`evals/relevance_gate.py` drives the real `RelevanceGate.decide` against the
real `participation.md` over labelled windows. Its own docstring: *"No agent, no
tools, no turn."*

It is the cleanest instrument in this repo and it is **not a turn**. The
withdrawn attempt's binary — either Nora is the subject or she is not — had no
room for it, and graded it with a third answer its own rule did not contain.

**This bundle must place it without a special case, or it is the same failure
again.** The candidate answer is that "which entry point" already covers it:
`RelevanceGate.decide` is an entry point exactly as `FirePath.speak` is. If that
holds, the taxonomy collapses into data and §3 stands.

### 4.2 The stores

The withdrawn attempt put the stores under *"not Nora — substituting one changes
what she is GIVEN, never what she IS"*, and then had to defend that.

`nora/app/eval_support.py:356-366` refutes it, measured: with the note store
unwired, a `-known` case stopped differing from its `-unknown` twin and the model
reasoned from the gap — *"A pair that cannot flip measures nothing."* The whole
`_KNOWLEDGE_CUT_TWINS` design demonstrates that **store content moves her
decision**.

Under §0 this needs no defending and no seam list. **The stores are config
behind the sandbox** — same implementation, seeded differently per case. Content
is input. There is no substitution to legislate because nothing is substituted;
a case says what she knows, which is the same kind of statement as what somebody
typed.

This is the clearest sign the operator's framing is the right one: the question
the previous attempt could not answer stops being a question.

## 5. What this does NOT decide

- **How the one composition is reached from `evals/`.** *(The prior question —
  whether the production root can be driven at all — is ANSWERED in `01-`: it
  can, and `tests/test_composition.py` does it ~90 times. The second builder
  exists because `evals/` may not import `tests/`, an import rule rather than a
  technical limit.)* What remains is which of `01-`'s three shapes is taken, and
  that is the operator's.
- **What happens to the reconstruction suites.** Cheap doctrine-wording probes
  have a use. Whether they survive under an honest name, or are rebuilt on the
  runner, or are deleted, is a decision this bundle must make and has not.
- **The model.** It is substituted routinely (`--model`, `--effort`,
  `_ALWAYS_SENDS`) and appeared on neither of the withdrawn attempt's lists.
  Under §3 it is presumably part of the subject and the override is a declared
  input, but that is asserted, not decided.
- **Order.** Which driver is absorbed first. `relevance_gate` is closest to the
  shape already; `adversarial` is furthest.

## 6. Why this is worth a bundle rather than a refactor

It changes eight drivers, touches `eval_support.py`'s three composition sites,
and must answer §4.1 and §4.2 before any of that is safe. Those are design
decisions.

And it has one property the last attempt lacked: **it can be wrong in a way that
shows.** If §4.1 cannot be expressed as an entry point, or the composition root
cannot be driven, the shape is refuted and this bundle says so rather than
shipping a rule nothing satisfies.

## 7. What must be true before any convention is written

From #311, and stated as the order rather than as requirements:

**Build the one runner first. Absorb the drivers into it one at a time. Write
the convention last, describing what exists.** A convention written first
describes eight variations and legitimises them, which is the mistake already
made once.

The corollary is the test of whether this bundle is working: **every driver
absorbed is one `_run_*` function deleted.** If the count does not fall, the
runner has become a ninth way to build a subject.
