---
kind: report
title: "Fast test pass — implementation report"
spec: docs/superpowers/specs/2026-08-21-fast-test-pass-design.md
timestamp: '2026-08-21'
---

# Fast test pass — report

## What shipped

- `Makefile`: `test-fast` (`uv run pytest -q -n 8`) and `test-affected`
  (dispatches on `scripts/affected_tests.py`'s output: `ALL` -> full `-n 8` run,
  empty -> nothing, else -> `-n 8` on the named targets). `test` and `gate`
  unchanged - still serial, still what CLAUDE.md's gate runs.
- `scripts/affected_tests.py`: the section 3 selector. `select()` is the pure
  decision function (list of changed paths -> target list or `ALL`);
  `git_changed_paths()`/`main()` wrap it in git. Rule order: (0) any
  `nora/doctrine/**` or `nora/dreamer/**` path forces `ALL`, checked before
  (1) all-paths-docs-only -> nothing, (2) config-only -> the two config test
  files, (3) tests-only -> those files, else -> `ALL`.
- `tests/test_affected_tests.py`: 15 tests - per-rule cases against `select()`
  directly, plus git-plumbing/CLI tests against a real temporary git repo.
- Spec section 6 updated in place with the measured numbers and the
  testcontainers answer.
- `release-fragments/unreleased/fast-test-pass.json` - `type: "feature"`.

## Measurements

- Serial (`make test`): ~420s (7m00s), 3741 tests (measured prior to this
  session's own added tests).
- `-n 8` (`make test-fast`), this session's runs: 96.17s and a repeat at
  104.76s during final verification, both `passed`/`skipped`/`xfailed` with
  **zero failures**. No order-dependent tests found under distribution.
- Testcontainers under `-n 8`: **one Postgres container per xdist worker**,
  not shared - `tests/conftest.py`'s `_pg_container` fixture is
  `scope="session"`, and xdist gives each worker its own pytest session/
  process, so the scope is per-worker. Observed via
  `docker ps --filter label=org.testcontainers=true` polled during a live
  `-n 8` run: container count climbed to a peak of 20 against a 2-container
  stale baseline, consistent with up to 8 workers x (1 Postgres + 1 paired
  Ryuk reaper) = 16 new containers. Confirmed again by inspecting `docker ps`
  immediately post-run, which still showed worker-owned Postgres+Ryuk pairs
  mid-teardown. Budget for up to 8 concurrent Postgres instances.

## Break-the-selector

Removed Rule 0 (the doctrine/dreamer precedence check) *and* the matching
exclusion inside `_is_docs_only`, so a `nora/doctrine/**` change fell through
to the generic docs-only rule. Result - exactly the three tests guarding that
ordering went red, all others stayed green:

```
FAILED tests/test_affected_tests.py::test_doctrine_markdown_change_selects_all
  AssertionError: assert [] == 'ALL'
   +  where [] = select(['nora/doctrine/conduct.md'])
FAILED tests/test_affected_tests.py::test_dreamer_markdown_change_selects_all
  AssertionError: assert [] == 'ALL'
   +  where [] = select(['nora/dreamer/doctrine/dreamer.md'])
FAILED tests/test_affected_tests.py::test_doctrine_change_outranks_docs_only_even_when_mixed_with_real_docs
  AssertionError: assert [] == 'ALL'
   +  where [] = select(['docs/README.md', 'nora/doctrine/voice.md'])
3 failed, 12 passed
```

Restored (verified against a saved copy, diff clean); `uv run pytest -q
tests/test_affected_tests.py` -> 15 passed.

## Real-world confirmation

`uv run python scripts/affected_tests.py` on this branch's real diff against
`origin/master` (the merged fact-tier work - touches `nora/doctrine/*.md`,
migrations, `nora/domain/**`, etc.) correctly printed `ALL`.

## Gate status

`ruff check nora/ evals/ tests/`, `mypy nora/domain nora/config.py nora/app
nora/infra nora/presence`, `lint-imports`, `check_release_fragments.py --base
origin/master` - all green. Full parallel suite green (`-n 8`, zero failures,
run twice this session with matching pass/skip/xfail shape both times).

## Review follow-up (round 2): tests-only rule silent-gap fix

**Critical, fixed.** The tests-only rule ran any changed path under `tests/`
verbatim, including non-collectible helpers (`conftest.py`, `builders.py`,
`fakes.py`, `objectives.py`) — pytest collects zero tests from those, so a
fixture-breaking edit selected a target that runs nothing and exits green.
Silent confident-wrong-subset, worse than the doctrine case (which at least
runs the wrong tests).

Fix: added `_is_collectible_test_file()` — basename matches `test_*.py` by
pytest's own default collection convention, not a hand-maintained filename
list (a list goes stale; a convention doesn't). The tests-only rule now
requires every changed path in the diff to satisfy it; one helper anywhere
sends the whole selection to `ALL`.

Four new tests added (19 total, all green): `tests/conftest.py` alone -> `ALL`;
`tests/builders.py` alone -> `ALL`; a real `test_*.py` mixed with a helper ->
`ALL` (helper dominates); a real `test_*.py` alone -> still narrowly selected.

**Verified the fix bites** by reverting it and rerunning:

```
FAILED tests/test_affected_tests.py::test_a_non_collectible_helper_alone_selects_all
  AssertionError: assert ['tests/conftest.py'] == 'ALL'
   +  where ['tests/conftest.py'] = select(['tests/conftest.py'])
FAILED tests/test_affected_tests.py::test_a_shared_test_helper_alone_selects_all
FAILED tests/test_affected_tests.py::test_a_real_test_file_mixed_with_a_helper_selects_all
3 failed, 16 passed
```

Restored (diff against saved copy clean); 19/19 green again.

**Important (not blocking):** spec's §2 justified `-n 8` against core count;
this session's own container measurement shows the binding constraint
observed in practice is one Postgres+Ryuk pair per worker — memory, not CPU.
Recorded in spec §6 item 3: the reasoning was incomplete (not wrong for
tonight — `-n 8` stays, measured working twice), and peak RSS across the
Postgres+Ryuk containers plus pytest workers is named as the number to
measure before ever raising the worker count.

Gates rerun in the foreground after the fix: ruff, mypy, lint-imports,
check_release_fragments — all green. `uv run pytest -n 8 -q`: 3738 passed,
1 skipped, 1 xfailed, zero failures, 100.34s.
