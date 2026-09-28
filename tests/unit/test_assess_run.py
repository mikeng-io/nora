"""`nora.app.assess_run` — the flag refusal and the render (design/
cognition-loop/30-cognition.md §6, DEC-240). No database: `run_nightly`
refuses before it opens a connection when `[assess] mode` is `"off"`."""

from __future__ import annotations

import pytest

from nora.app.assess_run import AssessDisabledError, render, run_nightly
from nora.domain.assess import AssessReport, DqCheckResult


async def test_run_nightly_refuses_when_mode_is_off():
    with pytest.raises(AssessDisabledError):
        await run_nightly(engine=object(), mode="off")  # type: ignore[arg-type]


def test_render_marks_ok_fail_and_not_applicable():
    report = AssessReport(
        kind="nightly_dq", run_at_ms=1234,
        results=(
            DqCheckResult(check="unresolved_citations", status="ok"),
            DqCheckResult(
                check="empty_lineage_rows", status="fail", failing=3,
                sample=("a", "b"), detail="expected before step 3 writers",
            ),
            DqCheckResult(
                check="sections_past_age_horizon", status="not_applicable",
                detail="step 8 has not shipped",
            ),
        ),
    )
    lines = render(report)
    assert lines[0] == "assess nightly_dq @ 1234"
    assert any("[OK] unresolved_citations" in line for line in lines)
    assert any(
        "[FAIL] empty_lineage_rows (3): a, b" in line
        and "expected before step 3 writers" in line
        for line in lines
    )
    assert any("[N/A] sections_past_age_horizon" in line for line in lines)
