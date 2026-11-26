# Test selection

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Ftest-selection%2F%22)

Automatic validation targets three minutes and has a five-minute ceiling.
The automatic backend baseline runs unit/domain tests plus the cheap CI plumbing guards, excluding the measured
slow modules in `scripts/ci_fast_tests.py`. It has a 170-second interrupt deadline
and a further ten seconds for teardown. Timing contracts stay on the manual
path; the automatic baseline must leave headroom for process startup and cleanup.
Timeout fails the check.

Complete acceptance is on demand: dispatch the **CI** workflow at the desired
branch/ref. It runs every backend test, including SQL contracts, integration,
architecture and serial timing checks. It also records the dependency map when
run successfully on master. Release PRs use the automatic baseline and never
wait for a full run. Automatic success does not attest to complete acceptance.

The component classifier still selects Python, frontend, Go and security checks.
Automatic component jobs are limited to four minutes; classification and the
final join are short overhead. Hosted elapsed time must be read back before
claiming the five-minute overall target is met. If a component cannot fit, move
that expensive validation to the manual path rather than increasing the budget.

Python CodeQL is on demand through manual CI dispatch; its hosted analysis exceeded
the automatic budget. Other selected security scans remain automatic. A skipped
Python scan is no claim that Python security analysis passed.

Local affected-test selection remains the union of the static import graph,
recorded dependency map and reader scan. Missing evidence still selects ALL;
this no longer launches the full backend suite automatically in hosted CI.

[ADR 0038](../../docs/adr/0038-bound-automatic-validation-and-run-full-tests-on-demand.md)
records the accepted tradeoff: feedback speed over automatic complete acceptance.
Reopen the baseline only with measured escaped failures or measured budget excess.
