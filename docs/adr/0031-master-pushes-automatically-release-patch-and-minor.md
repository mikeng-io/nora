# 0031 · Master pushes automatically release patch/minor; major stays owner-selected

- **Status:** accepted (operator, 2026-10-28)
- **Date:** 2026-10-28
- **Reverses, in part:** ADR 0027 Manual release amendment; its historical runtime-only eligibility and automatic marker-major policy
- **Index:** existing DEC-314 amendment, no new ID

## Context

The operator explicitly reversed PR317's manual/daily cadence on 2026-10-28.
Native API readback confirms Devin release automation disabled with no invocation;
[issue #316](https://github.com/mikeng-io/nora/issues/316) is closed as not planned.
The repository already has one generated metadata PR, a reachability planner,
required checks, a repository-scoped App, native auto-merge and the existing
release-images/deployments/Komodo path. No new evaluator/service/state is needed.

## Decision

Release all ordinary master changes automatically. Default patch; an explicit
Release-Bump: minor footer records the product author's milestone decision.
Breaking markers stop automatic planning before version writes. Owner-only
dispatch/rerun can explicitly select major on an exact full current source SHA.
Generated metadata alone auto-merges through required checks, strict branch
freshness, a single source-bound commit and exact-head match; no bypass or token
fallback. Publish merged metadata before planning again; refuse manifest/tag
mismatch to avoid duplicate versions. Generated commits/merges are excluded
from eligibility. Preserve all reachability and historical versions/tags.
Stable Releases feed the existing complete image/deployment route. Optional
master images remain default-off pending installed-wrapper readiness.

## Consequences

Ordinary maintenance and docs can produce patches. No daily agent/worth threshold
remains. Concurrent changes can collect into one pending release without omission.
Publication, image promotion and verified deployment remain distinct claims.

**Worse:**

- More stable versions and complete image builds, including internal-only work.
- A misdeclared product milestone produces patch; authors own the explicit footer.
- A broken required check or breaking range halts releases until corrected or
  explicitly owner-prepared as major. No bypass is introduced.
- Actions concurrency can coalesce pending pushes; each run therefore uses latest
  master and plans the complete unreleased range.

## Rejected

Paid daily/AI assessment, commit-type automatic minor, automatic major, manual
metadata completion for every patch/minor, alternate tag publisher and new ledger.

This holds unless actual source/loop failures or unacceptable release/build cost
justify another operator ruling. ADR 0027 retains its historical decision text; this record owns the current cadence.

## Sources and enforcement

[GitHub auto-merge](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/automatically-merging-a-pull-request)
provides the required-check wait; no custom scheduler is needed.
[SemVer 2.0.0](https://semver.org/spec/v2.0.0.html) grounds supported public-interface
compatibility; the product milestone boundary above is the operator's policy.

DEC-314 names the enforcing tests in `tests/architecture/test_release_plan.py`
and `tests/architecture/test_contribution_content.py`. Publication and deployment
still require their separate readbacks.
