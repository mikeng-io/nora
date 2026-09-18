# GOAL 2 — the backbone, and the scenarios. Nothing else.

Hand this to a session. **This is not the port.** It is the spine that makes a
port possible, plus the one design question nobody has ever answered.

**Branch:** `feat/evaluation-rebuild` (PR #334, HELD — do not merge).
**Repo entry point:** `AGENTS.md`.
**Read first, in this order:** `07-sdk-native` → `08-the-sdk-surface` →
`01-boundaries` §2 → `04-the-convention` §5. Reading the older nodes first is
how the last session rebuilt a harness that already existed.

---

## The basis. Fixed. Not yours to re-argue.

> **The evaluation suite IS `strands-agents-evals`.** The SDK is the main body.
> Four layers, two of them ours:
>
> 1. **SDK's, entirely** — corpus (`Case`), harness (`Experiment`), report,
>    caching, CLI, detectors, generators, simulators, chaos.
> 2. **SDK's** — grading a reply's CONTENT: built-in evaluators plus one
>    confidence-weighted `Evaluator` subclass.
> 3. **OURS** — the subject: `nora/subject/obtain(spec)`, the entry points,
>    seeding, isolation, async, teardown, clock, DSN scrubbing.
> 4. **OURS** — grading an ABSENCE. 75 of 150 cases expect one and no SDK
>    built-in measures it.

If you find yourself writing a runner, a report renderer or an outcome type,
**stop** — that is layer 1 and it is already written. `08-the-sdk-surface` §2.1.

---

## Scope. Two deliverables, and a hard edge.

**IN:** one entry point, end to end, SDK-native. The scenario decision.
**OUT:** porting thirteen corpora. Fixing the gate's span problem. Red teaming.
Simulators. Chaos. The judge's latency. Deleting `evals/rebuilt/`.

A backbone that works for one entry point and one corpus is the deliverable. If
it works, the port is mechanical and is somebody else's day. If it does not,
you have saved that day.

---

## Step 0 — the falsification that decides everything else

Before writing a spine, answer the question `08-the-sdk-surface` §6 names.

Drive **`participation`** (Agent-backed, so `08` §4.2 says its spans should
map) through `Case` + a `TracedHandler` + `run_evaluations_async(max_workers=1)`
+ **one** built-in `TRACE_LEVEL` evaluator. Three cases is enough.

**Done when:** you can print the `Session` that reached the evaluator and it
contains her actual reply text and the actual user message.

**If the Session is empty**, §1 and §4.2's surviving half both fall, only
`OutputEvaluator`-shaped grading on a string remains, and **that is the
finding** — write it down and stop. Do not route around it by hand-building a
trajectory dict; that is the `setdefault` trap in `08` §2.2 and it fails
silently.

Two traps to respect, both already measured:

- `TracedHandler.after` uses `processed.setdefault("trajectory", session)`. If
  your task returns its own `"trajectory"`, the Session is **discarded
  silently**.
- `@eval_task`'s wrapper is **sync and never awaits**, and every entry point is
  `async def`. Unhandled, it grades `'<coroutine object ...>'` and reports a
  score. Decide which escape you take (`08` §4.6) and **write down why**.

---

## Step 1 — the spine

One module. It holds only layer 3 and layer 4.

- **The task function** over `obtain(spec)` for ONE entry point. Per-case room
  and workspace isolation, the clock from the case, async teardown. Note that
  `EvalTaskHandler`'s hooks are **sync** and `_close()` is not, so the
  isolation lives in the task body (`08` §3.3).
- **It must never raise.** `Experiment._worker` catches and writes
  `test_pass=False, score=0` for every evaluator **without calling one**, so an
  ERROR becomes a FAIL below anything you can write. Every exception is caught
  in the task, scrubbed of the DSN, and returned as data.
- **The absence grader** — layer 4, the one no SDK supplies. It must
  distinguish *correctly said nothing* from *nothing happened because something
  broke*, reading the fail-closed signal the task carries. `04-the-convention`
  §5.1 measured why: with no provider, three of four entry points return a
  VERDICT rather than an error.
- **The ERROR convention.** `test_pass` is a `bool`. Decide how ERROR is
  carried — `label`, a reserved `reason` prefix, whatever — and write the rule
  down where the gate can read it. An ERROR that reads as a FAIL is this
  bundle's rule 3 broken, and it is now broken *by the library*, not by us.

No corpus format work. Hand-write three `Case` objects in Python.

---

## Step 2 — the scenarios. This is the part nobody has done.

The thirteen corpora and 150 cases are **an accretion, not a decision.** They
grew case by case out of incidents. Nobody has ever written down what this
suite must measure and why.

Answer it, in a new node, from evidence rather than from the existing files:

1. **What does Nora actually do** that can be wrong in a way a user would
   notice? Derive it from her entry points and from `design/`, not from
   `evals/corpora/`.
2. **What has actually broken?** Read the issues and the design bundles'
   recorded incidents. A scenario class earns its place by having failed, or by
   being something whose failure would be silent.
3. **What is worth a model call?** 75 of 150 cases grade an absence, 36 grade a
   reply. Is that the right shape, or is it the shape the old grader could
   express?
4. **What is missing entirely?** `08-the-sdk-surface` names two: nothing asks
   what she does when a **tool fails** (`chaos`), and `participation` asks a
   **multi-turn** question in single-turn form. Find the rest.
5. **Which existing corpora do NOT earn their place?** `06-first-full-run` §1
   records four `no_trigger` cases that were copies of `plain_no_mention`
   wearing four rule names, and 8 of 16 that passed with both matching rules
   deleted. Name every case you would drop, with the reason.

**Deliverable:** a scenario map — classes, what each measures, which SDK
instrument grades it, and which are absences. Not cases. The cases follow.

**This is a decision node, so it carries a decision.** Reserve the number, put
the recommendation in, and leave it for the operator.

---

## The disciplines. Non-negotiable, and each one has already cost this repo.

1. **Falsify every pin before trusting it.** Break it, watch the named test go
   red, restore. A pin that has never failed is a pin nobody has checked, and
   **a pin whose condition cannot occur is not a pin** — the `unseeded` guard
   in `entry.py` was retracted this week for exactly that.
2. **ERROR is not FAIL and is never PASS.** The library now breaks this for
   you. Your task and your absence grader must not.
3. **No claim without a tool call, and no probe you did not run.** Last session
   printed `getattr(Evaluator, "__abstractmethods__", None)` and reported the
   default as a measurement. In this bundle, of all places.
4. **Read a gate's exit code, never through `tail`.** The pipe returns `tail`'s
   status.
5. **`make gate` is the FINAL check, not the iteration check** (`AGENTS.md:317`).
   Code → `make test-affected`. **Docs-only → `make release-check` alone**;
   `affected_tests.py` returns `ALL` for a docs change and runs all 7,899 tests,
   so it does not help you.
6. **No absence claim without naming what you searched.**
7. **A late reversal is not landed until every quoting file changes.** Three
   reviewers found stale prose in six files after the last correction.

---

## Definition of done

- A populated `Session` printed from Step 0, or the finding that it is empty.
- One entry point graded end to end by `strands_evals.Experiment`, with an
  absence case and an ERROR case both landing correctly.
- The ERROR convention written down where the gate can read it.
- The scenario map, with a reserved decision and a recommendation.
- No runner, no report renderer, no outcome type written.
- `make test-affected` green by exit code; one release fragment.

## What would STOP this and should be escalated

- The `Session` is empty for an **Agent-backed** entry point. That refutes
  `08-the-sdk-surface` §1 and the plan needs rewriting, not routing around.
- The async escape forces `max_workers=1` **and** the suite is then too slow to
  run. That is a real constraint on the whole approach and the operator decides
  it, not you.
- The scenario map implies the thirteen corpora are mostly wrong. That is a
  finding worth having, and it is the operator's call what happens to them.
- Anything that would change what the live deployment does.
