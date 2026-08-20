---
kind: spec
title: "The fast pass: parallel by default, change-aware selection, and a gate that never shrinks"
description: "Editing three markdown files costs a seven-minute suite. This adds two things and refuses a third. It parallelises the suite (pytest-xdist over 16 cores), and it selects tests from what actually changed — with a docs-only path that provably needs no Python test at all. What it does NOT do is let the merge gate shrink: selection is for the edit-run-edit loop, `make gate` stays whole, and any change the selector cannot map with confidence falls back to running everything. A fast path that silently skips a test somebody needed is worse than a slow one."
tags: [nora, spec, tests, tooling, xdist, selection]
timestamp: '2026-08-21'
status: proposed
---

# The fast pass

## 0. The complaint, and what is actually wrong

The operator, 2026-08-21:

> *"It does not make sense that sometimes when you fix several files — maybe two
> to three markdown files — you have to run over 10 minutes of test files."*

He is right, and the diagnosis is two independent problems wearing one symptom:

1. **The suite is serial.** 3721 tests on a 16-core box, run one at a time.
2. **The suite is undiscriminating.** A markdown edit and a schema change cost
   exactly the same, because nothing looks at what changed.

Fixing (1) helps every run. Fixing (2) is what makes a docs edit cost seconds.
They are separate changes and this document keeps them separate, because (1) is
safe by construction and (2) is only safe if it is built to fail closed.

## 1. What this must not become

**The merge gate does not shrink.** CLAUDE.md's four commands stay exactly as
they are, and `make gate` keeps running the whole suite. Everything here serves
the *iteration* loop — the edit, run, edit cycle — where waiting seven minutes
to learn a docstring was fine is pure waste.

This distinction is the whole safety argument, so it is stated once, plainly:

> **A fast path that silently skips the test somebody needed is worse than a
> slow one**, because a green result now means something weaker than it did
> before, and nobody re-reads the tool that told them so.

Which is why §3's selector fails closed: anything it cannot map with confidence
runs the full suite. The failure mode it must never have is a confident wrong
subset.

## 2. Parallelism — `pytest-xdist`

`uv add --dev pytest-xdist`, then `-n` at the call site. Safe by construction:
the same tests, the same assertions, distributed.

Two things to verify rather than assume, both recorded in §6:

- **Testcontainers under xdist.** The Postgres fixture's scope decides whether
  workers share one container or spawn one each. Sharing is faster; spawning is
  more isolated and heavier. Whichever it does, the number must be known, not
  discovered by a machine falling over.
- **Order-dependent tests.** Distribution reorders execution. A test that passed
  only because another ran first will now fail — and that is a *finding*, not a
  regression to paper over. Any such test is already broken and was hiding.

`-n 8` rather than `-n auto` on a 16-core box shared with Docker, model calls and
other agents: `auto` takes all cores and starves the containers the suite itself
depends on.

## 3. Selection — change-aware, fail-closed

`scripts/affected_tests.py`, taking a base ref (default `origin/master`) and
emitting either a pytest path list or the string `ALL`.

The rules, in order, first match wins:

| changed paths | run |
|---|---|
| **only** `*.md`, `docs/**`, `release-fragments/**`, `design/**` | **nothing** — plus the docs gates (`check_release_fragments.py`) |
| only `config.toml` / `config.example.toml` | `tests/test_config.py tests/test_env_template.py` |
| only `tests/**` | exactly the changed test files |
| any `nora/**`, `evals/**`, `pyproject.toml`, `Makefile`, `uv.lock`, migrations, or anything unrecognised | **`ALL`** |

**The docs-only row is the one that earns its keep**, and it is the only row that
is *provably* safe rather than heuristically safe: no Python imports a markdown
file at runtime. The exception is deliberate and narrow — `nora/doctrine/**` and
`nora/dreamer/**` are markdown that IS loaded and hashed, so they are **not**
docs for this purpose and route to `ALL`. Getting that backwards would let a
doctrine edit skip the doctrine tests, which is exactly the class of mistake
this tier's own spec spends its length arguing against.

**No import-graph inference.** A static module→test map was considered and
rejected (§5): the tests most worth running after a change to `nora/language/`
are the integration tests that reach it through composition and never import it
by name. A selector that looks confident and misses those is the failure mode
§1 forbids.

## 4. Interface

```
make test          # unchanged — the full suite, serial. What the gate runs.
make test-fast     # full suite, parallel (-n 8)
make test-affected # §3 selection, parallel. The iteration loop's command.
make gate          # unchanged: lint typecheck imports test
```

`make gate` deliberately keeps calling serial `test`, not `test-fast`, until §6's
xdist questions are answered on real runs. Making the gate parallel is a second
decision, not a free consequence of this one.

## 5. Rejected

- **Static import-graph selection** — misses integration tests that reach a
  module through composition rather than by importing it. High confidence,
  wrong answers.
- **`pytest-testmon` / coverage-based selection** — accurate, and it needs a
  coverage database that goes stale, is per-machine, and is one more thing that
  can be wrong without saying so. Revisit if §3's coarse rules prove too coarse.
- **Shrinking `make gate`** — §1.
- **`-n auto`** — §2.

## 6. What must be true before it ships

1. **xdist measured against serial on this box**, both wall-clock numbers
   recorded, not estimated: serial full suite **~420s (7m00s)**, 3741 tests;
   `-n 8` **96.17s** (a repeat run measured 98.17s — consistent, both well
   under two minutes). 16 cores available; `-n 8` is a deliberate half, per
   §2, to leave room for Docker and everything else sharing the box.
2. The full suite **passes under `-n 8`** with the same count as serial:
   `3719 passed, 1 skipped, 1 xfailed`, **zero failures**. **No test failed
   under distribution** — the main risk this feature carried was order-dependent
   tests hiding in the suite (a test that only passed because another one ran
   first, and now runs on a different worker with no such neighbour). None
   were found. That is a finding worth recording on its own: the suite has no
   known hidden ordering dependency, at least none this reordering exposed.
3. **Testcontainers under `-n 8`: each worker spawns its own Postgres
   container**, not a shared one — `_pg_container` in `tests/conftest.py` is
   `scope="session"`, and xdist gives each worker its own pytest session (and
   its own Python process), so the scope is per-worker, not per-run. Observed
   directly: polling `docker ps --filter label=org.testcontainers=true` during
   a real `-n 8` run showed the container count climb from a 2-container
   baseline (stale leftovers from an earlier run) to a peak of 20 — consistent
   with up to 8 workers each starting one `postgres:18-alpine` container paired
   with its own `testcontainers/ryuk` reaper sidecar (2 containers/worker × 8
   = 16, plus the 2 baseline stragglers ≈ the observed peak). Confirmed by
   inspecting running containers immediately after the run, which still showed
   worker-owned Postgres+Ryuk pairs mid-teardown. **Memory cost: budget for up
   to 8 concurrent Postgres instances**, not 1 — this is why `-n 8` rather than
   `-n auto` on a 16-core box matters doubly (CPU **and** one Postgres per
   active worker).

   **Correction on the `-n 8` reasoning (not the number — the argument for
   it):** §2 justified `-n 8` against *core count* (half of 16). This
   measurement shows the binding constraint observed in practice is one
   Postgres+Ryuk pair per worker, i.e. **memory**, not CPU — a dimension the
   core-count argument never spoke to. `-n 8` is still the right number
   tonight (measured working, zero failures, twice), but the reasoning behind
   it was incomplete, not re-derived from the constraint that actually showed
   up. Before raising the worker count, measure **peak RSS** across the
   Postgres+Ryuk containers plus the pytest workers during a run — that is the
   number that would tell you whether `-n 16` fits the box, not core count.
4. `affected_tests.py` has tests of its own (`tests/test_affected_tests.py`),
   including: a docs-only change selects nothing; a `nora/doctrine/*.md` change
   selects `ALL` (and outranks the docs-only rule even when mixed with real
   docs changes in the same diff — the ordering §3 calls out as the one to get
   right); a `nora/dreamer/**` change selects `ALL`; an unrecognised path
   selects `ALL`; a mixed docs-and-`.py` change selects `ALL`; an empty diff
   selects nothing. Verified by deliberately breaking the doctrine-precedence
   rule (letting it fall through to the generic docs-only rule) and confirming
   exactly the three tests guarding that ordering went red, then restoring it.

   A second version of the same mistake was caught in review: the tests-only
   rule originally ran any changed path under `tests/` verbatim, including
   non-collectible helpers (`conftest.py`, `builders.py`, `fakes.py`,
   `objectives.py`) that dozens of test modules import but that pytest
   collects zero tests from — a change to one of those would have selected a
   target that runs nothing and exits green, a *silent* confident-wrong-subset,
   worse than the doctrine miss (which at least runs the wrong tests rather
   than none). Fixed: the tests-only rule now additionally requires every
   changed path to look collectible by pytest's own default convention
   (basename matches `test_*.py`, not a hand-maintained filename list), and
   one non-collectible path anywhere in the diff sends the whole selection to
   `ALL`. Verified the same way: reverted the fix, confirmed
   `test_a_non_collectible_helper_alone_selects_all` (and its two siblings)
   went red with `assert ['tests/conftest.py'] == 'ALL'`, restored it.
5. `make gate` still runs the whole suite.
