"""The Assess adaptor's own vocabulary: the nightly, deterministic
data-quality checks and the report Assess writes about them.

**Assess reports only.** It is not in `nora.infra.sql.history.HISTORY_TABLES`
or `APPEND_ONLY`'s write path for any tier: `assess_report` is the one store
this adaptor writes (reports and metrics only).
Nothing reads an `assess_report` row on a turn; it exists for the operator
and for `nora_dq_failures_total{check}`.

**Fail-closed, not fail-silent** (AGENTS.md's Measure spine, `CMS.md`): a
check that cannot run because the tier it checks does not exist yet is
`not_applicable`, never `ok` — a check that
silently reports zero failures because it checked nothing would read exactly
like a check that ran and found nothing wrong, and those are different facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "DQ_CHECKS",
    "AssessReport",
    "DqCheckResult",
    "DqStatus",
]

#: Every nightly data-quality check. Written out, not derived, because this IS
#: the vocabulary — `nora.app.assess_dq` has exactly one function per entry,
#: and `tests/unit/test_assess_dq.py::test_every_named_check_has_a_function`
#: ties the two so a renamed or dropped check cannot drift silently.
DQ_CHECKS: tuple[str, ...] = (
    "cross_room_duplicate_events",
    "rollup_rebuild_divergence",
    "happened_place_mismatch",
    "sections_past_age_horizon",
    "induced_line_no_aggregate",
    "empty_lineage_rows",
)

#: `ok` — the check ran and found nothing. `fail` — the check ran and found
#: at least one violation (`failing > 0`). `not_applicable` — the check could
#: not run because what it checks does not exist yet on this deployment (a
#: tier not shipped, a table not migrated): distinct from `ok` on purpose, so
#: a dashboard reading `ok` never means "checked, nothing wrong" when it
#: really means "nothing here to check yet". `error` — the check itself
#: raised (a query error, a connection drop, a programming bug) rather than
#: running to a verdict: distinct from both `ok` and `fail` on purpose, so
#: "the check ran clean" is never conflated with "the check never finished",
#: and distinct from `not_applicable` because an error is a live problem, not
#: an honestly-skipped check (`AGENTS.md`'s Measure spine: an ERROR is never
#: a PASS).
DqStatus = Literal["ok", "fail", "not_applicable", "error"]


@dataclass(frozen=True, slots=True)
class DqCheckResult:
    """One check's outcome. `sample` holds at most a handful of the
    offending keys (never a body, never a member name) — enough for an
    operator to look the row up, never enough to be the finding itself."""

    check: str
    status: DqStatus
    failing: int = 0
    sample: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if self.check not in DQ_CHECKS:
            raise ValueError(f"{self.check!r} is not a name in DQ_CHECKS")
        if self.status == "ok" and self.failing:
            raise ValueError("status=ok cannot carry a nonzero failing count")
        if self.status == "not_applicable" and self.failing:
            raise ValueError("status=not_applicable cannot carry a failing count")
        if self.status == "fail" and not self.failing:
            raise ValueError("status=fail needs a nonzero failing count")
        if self.status == "error" and self.failing:
            raise ValueError("status=error cannot carry a failing count")


@dataclass(frozen=True, slots=True)
class AssessReport:
    """One row of `assess_report`: one nightly run's results, kept as
    written — Assess never rewrites a report, it writes the next one
    (`nora.infra.sql.history.APPEND_ONLY`)."""

    kind: Literal["nightly_dq"]
    run_at_ms: int
    results: tuple[DqCheckResult, ...] = field(default_factory=tuple)

    @property
    def failing_checks(self) -> tuple[str, ...]:
        return tuple(r.check for r in self.results if r.status == "fail")

    @property
    def errored_checks(self) -> tuple[str, ...]:
        return tuple(r.check for r in self.results if r.status == "error")

    @property
    def total_failing(self) -> int:
        return sum(r.failing for r in self.results)
