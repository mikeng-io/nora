# Test selection by what a change touches

Operator ruling: a change runs the tests it can affect and no others. A one-word
front-end edit or a renamed local variable must not buy the 21,000-test suite.
The decision number is allocated from current `origin/master` immediately before
merge (`nora-decision`); this draft does not reserve one.

## Intent

`scripts/affected_tests.py` answers "which tests?" from the diff. Today it knows
documents and tests, and answers `ALL` for everything else, so most pull
requests run the whole suite: 61% of 57 sampled PR runs did, median 20.5 minutes
for the required `contracts` job. The suite is not slow per test: 77% of cases
run under 50 ms and about 800 cases (3.7%) hold 69% of the time. The cost is
that unrelated tests run at all.

After this change a pull request runs the tests whose recorded dependencies
intersect its diff, found from measurement of the real suite, never from a
hand-kept list. Every path the measurement cannot answer for runs everything
(fail closed). The whole suite still runs on every `master` push and weekly, and
is the evidence the release waits for.

This holds unless the post-merge full run fails a test the pull request's
selection skipped twice within 30 days; then the dependency record is
unsound and the file-read recorder under *Upgrade path* replaces the guess.

## Evidence this rests on

Measured on 2026-11-07 against `e46b21cb5`, unit and architecture tests
(12,082 cases, 4 workers):

| measurement | result |
|---|---|
| per-test coverage, one changed source file | median 1% of test files, p90 2%, max 17% |
| `pytest-testmon` 2.3.0, one inserted statement in `nora/agent/arithmetic.py::_eval` | 24 of 132 tests selected, the same two test files the coverage record names; 0 from an unrelated file |
| static import closure (grimp) over the whole suite | median change selects 48% of test time: a composition-root hub is imported by 742 of 882 test files, so imports alone cannot narrow |
| `pytest-testmon` with this repo's `addopts = "-m 'not network'"` | selection deactivates silently unless `--testmon-forceselect` is given |
| `pytest-testmon`, edit to `design/09-decision-index.md` read by `test_decision_index_tests_exist` | NOT selected: it does not see files a test reads as data |

Not measured: the narrowing for integration, contract and infra tests (they
need Postgres and Redis containers). The first full recording produces that
number; the builder reports it.

## Mechanism

Two sources of dependency, each used for what it can see, one function that
combines them.

**Python code: `pytest-testmon`.** A full run records, per test, the code blocks
it executed and the module-level code of what it imported, as content checksums
in one SQLite file. A later run selects tests whose recorded blocks changed. It
is function-level, covers import-time changes, works under `-n`, and compares
content, so a database recorded at any `master` commit selects correctly for a
branch cut from another. It is a maintained third-party tool; this repo does not
re-implement it.

**Everything else: the existing reader scan.** `_readers` already finds, from
string literals, which tests read a given non-Python path. Its coverage widens
from documents to every non-`.py` path outside `nora/`: `app/`, `deploy/`,
`scripts/` non-Python, configs, and `.py` files that tests only run as
subprocesses (their dotted module name, `scripts.release_plan`, counts as a
literal). A path no test names selects nothing: a portal word change selects
the portal job plus the two Python tests that read `app/src`.

What the combined selection cannot see, stated so the omission is honest: a
module imported through `importlib` or any computed string
(`import_module("pkg." + name)`) leaves no literal to match and no static
edge to follow — it is NOT covered. Test-module `importlib` imports were
already a blind spot; this extends it honestly to production imports, pinned
by a named limitation test, not pretended away.

**Stays `ALL`, deliberately:**

- any non-`.py` file under `nora/`: doctrine, skills and prompts are opened by
  loader code at a path computed at runtime, which neither source can see;
- `pyproject.toml`, `uv.lock`, `Makefile`, `Dockerfile`, `docker-compose.yml`,
  `.github/workflows/**`, `conftest.py`, `tests/builders.py`: they change the
  environment or every test;
- a missing, unreadable, corrupt or environment-mismatched dependency record
  (fail closed: ERROR is never NONE);
- a selector crash (existing behaviour).

`tests/architecture/` continues to run whole when any test file changes, as
today, because those tests read the test tree.

**The record.** The post-merge full run — every non-pull-request event: push,
schedule, dispatch — runs the whole suite, writes the testmon database and
uploads it as the `testmon-data` artifact, named for the commit and for a hash
of `uv.lock` + `pyproject.toml` (+ Python version). A pull request, and
`make test` locally, downloads the newest artifact whose dependency hash
matches **and whose producing run is a completed, successful `ci.yml` push or
schedule run on `master` in this repository** (not a pull-request run, another
workflow, a fork head, or a different commit); none means `ALL`. One
storage mechanism for both, no cache keyed per branch.

**Release.** A release PR changes only the version files, on a tree whose
content already passed the full run on `master`. Its `contracts` job therefore
does not run the suite — but only when the bypass is proven: the head is the
release branch in this repository (never a fork) AND the base…head diff touches
only `{.release-please-manifest.json, CHANGELOG.md, nora/__init__.py,
pyproject.toml, uv.lock}` with only version lines inside the strict three.
Anything else runs the normal selected acceptance, never a silent pass. When
eligible, it waits for the full run on the PR's base commit and passes only if
that run concluded `success`. Missing, cancelled, failed or
timed-out is a failure, never a skip, so the release cannot publish a commit the
full suite has not passed.

**The escape measure.** After the post-merge full run the job writes to its step
summary: failed tests, and for each whether the merged diff's selection included
it. A red test the selection skipped is an escape. The counter is the reversal
condition above.

## Obligations (the PR must show each, with evidence)

1. **Spike first, report before building on it.** Verify on this repo: the
   database is portable between checkouts at different absolute paths (CI runner
   to a developer worktree); a read-only mode exists for pull-request runs; one
   run covers `scripts/pytest_gate.py`'s two phases (parallel, then the serial
   timing contracts) without corrupting the record; its size from a full
   recording including container tests; the narrowing number for the integration,
   contract and infra tests. If an assumption fails, stop and report; the
   mechanism changes, the obligations do not.
2. **One selection function** used by `make test`, `make gate`, CI and the
   release check. Its stdout contract (`ALL` / `NONE` / targets) either stays or
   is changed in `Makefile`, `scripts/ci_plan.py`, its docs and
   `tests/test_affected_tests.py` together.
3. **Fail-closed tests**, each red when its production line is reverted
   (`nora-prove-fix`): missing record, corrupt record, hash mismatch and
   `nora/` non-`.py` each yield `ALL`, never `NONE`; a record that selects
   nothing for a `.py` change is `NONE` only when the record is valid.
4. **Selection tests on a generated mini package** (no production rows, no
   mocks that answer every call the same way): a function-body edit selects only
   its dependent test; a module-level constant edit selects the importing
   tests; a data file a test reads selects that reader; an unread path selects
   nothing. Positive control beside every zero-assertion.
5. **CI**: `master` push and the weekly schedule run the full suite with
   recording and upload the database; pull requests use the downloaded record;
   unavailable record means `ALL`; the `checks` join still rejects skipped or
   missing selected jobs. Timing contracts stay serial.
6. **Release PR check** as above, tested through `scripts/ci_plan.py`'s own
   verify path with success, failure, pending and missing run states.
7. **Escape report** in the full-run step summary, with a test of its
   arithmetic on a generated failure.
8. **Docs, present tense, rewritten in place:** `tests/README.md`,
   `AGENTS.md` «Gate commands», `docs/maintainers/conventions-rationale.md`
   «Test structure» where they describe selection; the `affected_tests.py`
   module docstring (its "nora/** is ALL" rationale is replaced by this one).
   An ADR and a decision-index row via `nora-decision` at merge.
9. **Reported in the PR:** before and after wall time for four real diffs (a
   portal string, a renamed local in `nora/`, a module-level constant in
   `nora/`, a doctrine `.md`), the recording's size, and the escape-report
   output from at least one full run.

## Non-goals

- Deleting, merging or rewriting tests; the slow redundant ones named in the
  audit (`test_acceptance_rules`, the repeated source scans) are separate work.
- A slow tier, markers, or a second tier of required checks.
- The portal, Go, site and CodeQL jobs; `ci_plan.classify` keeps its job routing.
- A custom coverage engine (see *Upgrade path*).

## Upgrade path

If testmon proves unsound or unmaintained: record per-test code coverage
(`coverage` contexts, measured above) plus per-test file opens through
`sys.addaudithook` (`open`, `os.scandir`), in one `scripts/pytest_depmap`
plugin beside `pytest_cost`. That closes the data-read blind spot generally and
retires both the reader scan and the `nora/` non-`.py` `ALL`. It is not built
now: nothing has failed yet.

## Delivery coverage

Obligations 1 to 9 above; non-goals are tracked in the audit, not here.
