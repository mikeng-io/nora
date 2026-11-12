# Test selection

A change runs the tests its diff can affect. Selection is derived from measured
and statically discoverable dependencies, never a hand-maintained list. When
those sources cannot prove a narrow answer, the whole suite runs: a slow
correct answer is preferable to a fast wrong one.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Ftest-selection%2F%22)

[ADR 0036](../../docs/adr/0036-pull-requests-run-the-tests-their-diff-can-affect.md)
records why this replaced broad pull-request runs.

## Parts

- **The change classifier** interprets the diff path by path and unions the
  affected targets. One unclassified or environment-wide path makes the answer
  the whole suite.
- **The recorded dependency map** connects Python changes to tests that
  executed the changed code, including import-time behavior represented in the
  recording. Content checksums make the record usable across checkouts and
  branch bases.
- **The static import graph** covers collection-time and other import
  relationships that execution recording cannot see, including imports through
  production modules and test modules that import other tests.
- **The reader scan** finds tests that name or traverse non-Python inputs and
  Python entry points invoked as subprocesses. It is recomputed from the
  current test tree so a new reader does not require a registry edit.
- **The record pipeline** produces the dependency map from a successful full
  run on the main branch and makes a compatible record available to local and
  pull-request selection.
- **The release backstop** keeps full-suite acceptance on the main branch and
  scheduled runs. A version-only release change waits for proof that its exact
  base passed the full suite rather than treating selection as release
  evidence.

## Selection invariants

The three dependency sources answer different questions and are combined, not
treated as alternatives. Execution recording cannot see every data read or
collection-only import; the import graph cannot see computed dynamic imports;
the reader scan cannot resolve a path constructed entirely at runtime. Their
known blind spots therefore widen selection or remain explicit limitations,
never silently become `NONE`.

Shared test helpers, unreadable test sources, unresolved import layouts and
shared fixtures fail closed because their consumers cannot be bounded. Test
changes also select the architecture tests that inspect the test tree itself.
Runtime-loaded non-Python resources and build, workflow, dependency and test
environment changes select everything.

A missing, unreadable, corrupt or environment-mismatched dependency record is
unavailable and selects everything. A selector exception does the same. Empty
output is not a selection result: the output contract distinguishes the whole
suite, a proven empty target set and explicit targets so a crash cannot pass as
“nothing to run.”

## Full-suite and release boundaries

Pull requests use selection to spend test time where the diff can change a
result. The complete suite remains the independent check on that judgement and
the producer of future dependency records. A full-run failure that selection
would have skipped is evidence against the record or selection rules, not an
acceptable green release.

The release exception is deliberately narrower than ordinary selection. It
applies only to a trusted, strictly version-only change whose base already has
a successful full run. Missing, pending, cancelled, failed or mismatched
evidence refuses the exception.

## Why this shape

A static import closure alone selects too much around shared composition roots,
while execution recording alone misses files consumed as data and imports that
matter only during collection. Keeping separate sources for execution, imports
and readers preserves useful narrowing without pretending one instrument sees
everything.

The selector is shared by local iteration, the local gate and hosted checks so
their answers cannot drift. Full-suite runs remain separate evidence: selection
makes feedback proportional to a change; it never redefines what a correct
release is.
