# ADR 0038: Bound automatic validation and run full tests on demand

**Status:** accepted
**Date:** 2026-11-14
**Supersedes:** ADR 0036 for hosted execution policy; local selection remains
**Index:** DEC-327

## Context

[CI run 43459625923](https://github.com/mikeng-io/nora/actions/runs/43459625923/job/132004116543)
spent 28m35s on 22,162 parallel backend tests and 2m44s on 121 serial tests.
The dependency record was available, but the classifier selected ALL. Source-wide
checks and genuine security integration tests account for substantial cost.
Necessary coverage does not justify blocking every small change for half an hour.
The operator requires a three-minute target and five-minute upper bound.

## Decision

Automatic PR and master validation runs a bounded fast baseline. Backend testing
is interrupted at 170 seconds and killed after ten more seconds if teardown stalls;
automatic component jobs have a four-minute limit. Python CodeQL is manual-only
after its hosted run exhausted that budget; other selected scans stay automatic. Timeouts fail. The complete
backend suite is manual CI dispatch at an explicitly chosen ref, preserving SQL,
integration, architecture and timing tests. Release PRs use the same automatic
checks and do not wait for a full-suite run. Full dependency records are uploaded
only after successful complete manual validation on master.

Automatic success means fast-baseline success, never complete backend acceptance.
Actual hosted wall time, including setup and joins, must be measured; a timeout
configuration alone does not prove the overall latency target.

## Consequences

Small changes no longer fall back automatically to half-hour backend runs.
All tests remain available, and focused local validation remains impact-based.

**Worse:** regressions outside the fast baseline can merge until manual validation
finds them. Slow or broken automatic checks fail instead of extending the budget.
An operator must request complete validation when its evidence is needed.

## Rejected

Increasing workers or optimizing individual tests alone leaves the automatic
ALL fallback and release wait intact. Deleting boundary tests discards useful
manual evidence. Marking timed-out or unexecuted tests as passed misstates scope.
