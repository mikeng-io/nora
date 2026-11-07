# 0036 · Pull requests run the tests their diff can affect; the whole suite runs on master

- **Status:** accepted (operator, 2026-11-08)
- **Date:** 2026-11-08
- **Reverses, in part:** the test-selection rationale that every path no rule names runs the
  whole suite («a slow correct answer beats a fast wrong one», `scripts/affected_tests.py`
  before this change), for the paths below that now have a measured answer.
- **Design:** [test selection](../../design/test-selection/00-design.md).
- **Tracking:** [PR #644](https://github.com/mikeng-io/nora/pull/644).
- **Index:** DEC-327.

## Context

The required `contracts` job ran the whole backend suite for most pull requests. Of 57
sampled PR runs, 61% did; the job's median was 20.5 minutes against 1 to 4 minutes for every
other job. The suite holds 21,774 cases; 77% run under 50 ms and about 800 (3.7%) hold 69%
of the time, so the cost is that unrelated tests run at all, not that a few tests are slow.
A one-word portal edit paid for the downloader, queue and release-packaging tests.

The selector knew documents and tests, and answered `ALL` for every other path, by design:
a hand-kept map from sources to tests goes stale, and a confident wrong subset is the one
failure it must not have.

Measured on 2026-11-07 (unit and architecture tests, 12,082 cases):

- per-test coverage selects a median 1% of test files for one changed source file;
- `pytest-testmon` selected 24 of 132 tests for a one-function edit, the files the coverage
  record names, but it records only code executed during tests: a module that runs at import
  or collection time, and a data file a test reads, are invisible to it (a generated
  `LIMIT = make()` edited inside `make()` reports «changed files: 0» and the test then fails);
- a static import graph (grimp, already installed through import-linter) sees every static
  import including collection time, and cannot see imports made at run time.

Four review rounds then found, one layer at a time, the holes of a hand-written import
resolver and of a parsed-structure release check; each was closed by removing the class (one
maintained resolver; a byte-exact one-line version rule), not the shape.

## Decision

A pull request runs the tests its diff can affect, from two measured sources used for what
each can see, and everything else fails closed:

1. **Static import closure** (one grimp graph over production and test modules, parent-package
   edges added once) for collection-time and static-import effects. A scanned module the graph
   cannot name, a changed module absent from it, or a failed build is `ALL`.
2. **Recorded execution** (`pytest-testmon`, database written by every full run on `master`,
   consumed from the newest matching artifact whose producing run is this repository's
   `ci.yml` push or schedule run on `master`, completed, successful, same repository, same
   commit) for run-time execution including dynamic imports. No matching record is `ALL`.
3. **The reader scan** for files a test reads as data. Non-Python files under `nora/`,
   environment and build files, any `conftest.py`, and a path no source can answer are `ALL`.
4. **Every non-pull-request event runs the whole suite** with recording. This is the evidence
   a release waits for.
5. **A release PR does not rerun the suite.** If, and only if, the head is in this repository,
   the branch is the release branch, and the diff is the byte-exact one-line version bump of
   `nora/__init__.py`, `pyproject.toml`, `uv.lock` and the manifest plus added `CHANGELOG.md`
   text, all regular files, its `contracts` job waits for the whole-suite run on the PR's base
   commit and passes only if that run succeeded. Anything else runs normally.
6. **Escapes are counted.** After a whole-suite run on `master` the job reports each failing
   test and whether the merged diff's selection included it.

This holds unless the post-merge run fails a test the pull request's selection skipped twice
within 30 days; then the dependency record is unsound and the file-read recorder under
*Upgrade path* in the design replaces the guess.

## Consequences

**Better:** a portal, document, deploy-script or leaf-module change no longer runs the
backend suite; a release PR no longer repeats it; a missing or wrong record can only widen the
run, never narrow it.

**Worse:**

- Most Python changes still run everything. With the shared test helpers a change to a
  widely imported module reaches `conftest.py` and selects `ALL`: in the checked tree between
  75% and 94% of `nora/` modules. The narrowing for Python is therefore small until the
  recorder in the design's upgrade path replaces the static closure and the testmon record.
- A pull request no longer proves the whole suite before merge; the post-merge run, the
  release wait and the escape count are what stand behind it. A skipped test fails on
  `master`, not on the branch.
- `pytest-testmon` is a third-party dependency, and the record is a CI artifact with a
  retention window; a lapse means `ALL`, not an error.
- `importlib` and computed-name imports at collection time, and layouts other than
  repo-root packages, remain blind spots that fail closed only where they can be detected.

## Rejected

- **Static closure alone:** blind to execution at run time.
- **`pytest-testmon` alone:** blind to import-time code and to data files; it silently
  deselects a failing test.
- **A slow tier or markers:** a second set of required checks to maintain, with the same
  unrelated tests in the fast tier.
- **Skipping the suite on release PRs by branch name:** any pull request can name its branch.
- **Deleting tests:** a different question; it needs its own measurement.
