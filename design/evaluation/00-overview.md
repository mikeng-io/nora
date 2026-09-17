# Evaluation — the chain from a decision to a thing that can refute it

## 0. The question this bundle exists to answer

> **How does the evaluation suite establish that Nora behaves correctly, as
> designed and as expected?**

Operator, 2026-09-17. Everything here is downstream of that sentence, including
the plumbing question an earlier draft of this file mistook for the subject.

The answer today, measured: **it almost entirely does not.**

## 0.1 What is NOT in scope, and why it is worth saying first

**The test suite.** Unit, integration and e2e are a solved problem outside this
repo, and nothing about them is ours to invent:

> test case 嗰度其實冇乜需要太多嘢要 test … 你 search 返到底人哋 unit test 點做、
> integration test 點做、e2e test 點做，就已經完咗件事。嗰度其實冇乜嘢好傾。

That is right, and it also bounds this bundle. The test layer's blast radius is
*"do we catch a mistake before it ships"*. It does not reach runtime behaviour.
When it is disorganised, look up standard practice and follow it — do not write
a bespoke convention for it, which is what #113's remaining third would be if
taken further than it has been.

**What has no industry answer is the question in §0.** *"Does an LLM agent behave
as it was designed to"* is not unit testing at a larger scale, and the reason is
§3: the entire test discipline establishes that code is wired, and none of it
establishes how a model behaves once it is. That gap is ours, it is why this
bundle exists, and it is the only thing in it.

## 1. The chain

For a design statement to be more than prose, four links have to hold:

```
a design decision            DEC-n / R-n in design/09-decision-index.md
  → the behaviour it claims   what she must or must not DO
    → an evaluation that can refute it
      → driven against the real Nora
```

Break any link and the decision is unenforced.

## 2. Where it is broken, measured

`design/09-decision-index.md` at `b3c21686`. `AGENTS.md` sends readers here to
answer *"which test guards this?"*

**250 decision and rule rows:**

| | |
|---|---|
| cite an `evals/` instrument | **10** |
| cite a `tests/` unit test only | **196** |
| cite neither | **44** |

And of the ten:

| row | instrument | |
|---|---|---|
| R-14 | `evals/adversarial/` | **reconstruction** |
| R-53, R-54 | `source_scope.yaml` | **reconstruction** |
| DEC-75 | `identity_grounding.yaml` | **reconstruction** |
| R-83 | `being_contradicted.yaml` | **reconstruction** |
| DEC-76 | `journeys.py`, `evaluators.py` | real |
| DEC-45 | `harness.py` | real |
| DEC-30, DEC-33, DEC-58 | prose mention, no path | — |

**Two rows of 250 have a behavioural instrument that drives her. Five have one
that drives a bare model.**

### 2.1 The sharper cut — the rules

`R-*` rules are behavioural by construction: each says what she must or must not
do. **92 of them.**

| | |
|---|---|
| with an eval | **4** |
| unit test only | **52** |
| nothing at all | **36** |

Sampled, claim against guard:

- **R-7** — *"Per-tag tool-call budget; unknown tools default-deny."* → a file,
  no selector.
- **R-17** — *"A degraded derivation of Nora's own identity is counted, never
  merely logged."* → a file, no selector.
- **R-22** — *"A tool that could not answer says so in its RETURN VALUE, and
  'asked and nothing known' is never the same value as 'could not ask'."* →
  `test_honcho_representation.py::test_an_unknown_peer_is_reachable_and_empty_never_unreachable`

R-22's guard is a good test and it proves the **port** returns distinct values.
It cannot show that **she** keeps them distinct in a reply, which is what R-22 is
about. That is the normal case: **52 behavioural rules are guarded by tests that
measure wiring.**

## 3. The line, and why it is not a criticism of the tests

7,249 tests pass and they catch real defects. This is about which link they hold.

| | unit/integration | evaluation |
|---|---|---|
| `participation.md` is loaded, non-empty, at the pinned sha | ✅ | |
| the gate admits the windows it should | | ✅ |
| `_clip` marks a cut block | ✅ | |
| the grader reads the mark and changes its verdict | | ✅ |
| `send_reply` is registered with budget 1 | ✅ | |
| she stops at one reply when pushed | | ✅ |

Left column: *the code is wired*. Right column: *she behaves*. Standard practice
covers the left completely (§0.1). **The decision index's test column is a
left-column column, and `AGENTS.md` presents it as the answer to a right-column
question.**

## 4. Why the bare-model corpus survived as long as it did

It is one of the few behavioural instruments there are. With five of ten
citations pointing at reconstructions and 244 rows carrying no behavioural
reading at all, **a number from it had nothing to be compared against.** A 67%
reads as a 67% when it is the only reading in the room.

The reconstruction is not the disease. It is what the absence of the chain looks
like when somebody finally builds an instrument and nothing tells them what an
instrument is for.

## 5. What this bundle proposes

**The chain is the artifact.** A design statement that claims a behaviour names
the evaluation that can refute it, and that evaluation drives her.

**5.1 The index's test column splits.** Two answers, not one: what proves the
code is wired, and what would show the behaviour is wrong. Either may be absent —
but absent must READ as absent, rather than a wiring test standing in the
behavioural column. 36 `R-*` rules have neither today and say nothing about it.

**5.2 An evaluation is written against a decision, not against a capability.**
Today a suite exists because somebody wanted to measure a feature, so its
contents are whatever that person thought of. Under the chain it exists because a
decision claims something falsifiable, which makes *"what should this suite
contain"* answerable: **what would refute the decision it names.**

**5.3 Link 4 is a precondition, and it is `01-`'s subject.** An evaluation that
does not drive her cannot refute anything about her. The plumbing matters only
because of this, and not on its own.

## 6. What this bundle does NOT propose

- **That all 250 rows get an evaluation.** Most are not behavioural — a schema
  constraint, a key shape, a build rule. The 92 `R-*` rules are the population to
  ask, and 52 of them guarded by wiring is the finding.
- **That unit tests be replaced, or given a new convention.** §0.1.
- **A convention for writing evaluations.** That comes last, describing what
  exists. The attempt to write it first (PR #307) was withdrawn the same day.

## 7. The reading that would refute this bundle

If the 52 wiring-guarded rules are mostly rules whose behaviour is genuinely
settled by wiring — a budget that cannot be exceeded because the registry will
not allow it — then the gap is smaller than §2.1 makes it look and this bundle is
overstated.

**That is checkable and has not been checked.** Read the 52, classify each as
*settled by wiring* or *asserted about behaviour*, and report the split. Do it
before building anything, alongside `01-` §6's sizing.

A bundle about not trusting unmeasured claims should not ship one, and §2.1 is
currently the strongest claim here with the least work behind it.
