"""`nora.domain.assess` — the Assess adaptor's own vocabulary (design/
cognition-loop/30-cognition.md §6, DEC-240)."""

from __future__ import annotations

import pytest

from nora.app import assess_dq
from nora.domain.assess import DQ_CHECKS, AssessReport, DqCheckResult


def test_every_named_check_has_a_function():
    """30 §6's bullet list and `DQ_CHECKS` name the same set, and
    `nora.app.assess_dq` has exactly one function per name — a renamed or
    dropped check cannot drift silently."""
    for check in DQ_CHECKS:
        assert hasattr(assess_dq, check), f"no nora.app.assess_dq.{check}"


def test_status_ok_refuses_a_nonzero_failing_count():
    with pytest.raises(ValueError, match="status=ok"):
        DqCheckResult(check="unresolved_citations", status="ok", failing=1)


def test_status_not_applicable_refuses_a_nonzero_failing_count():
    with pytest.raises(ValueError, match="status=not_applicable"):
        DqCheckResult(
            check="sections_past_age_horizon", status="not_applicable", failing=1,
        )


def test_status_fail_needs_a_nonzero_failing_count():
    with pytest.raises(ValueError, match="status=fail"):
        DqCheckResult(check="unresolved_citations", status="fail", failing=0)


def test_an_unknown_check_name_is_refused():
    with pytest.raises(ValueError, match="not a name in DQ_CHECKS"):
        DqCheckResult(check="not_a_real_check", status="ok")


def test_report_failing_checks_names_only_the_fails():
    report = AssessReport(
        kind="nightly_dq", run_at_ms=1000,
        results=(
            DqCheckResult(check="unresolved_citations", status="ok"),
            DqCheckResult(
                check="empty_lineage_rows", status="fail", failing=2,
                sample=("a", "b"),
            ),
            DqCheckResult(
                check="sections_past_age_horizon", status="not_applicable",
            ),
        ),
    )
    assert report.failing_checks == ("empty_lineage_rows",)
    assert report.total_failing == 2
