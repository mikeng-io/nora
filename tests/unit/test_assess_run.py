"""`nora.app.assess_run` — the flag refusal and the render (DEC-240). No database: `run_nightly`
refuses before it opens a connection when `[assess] mode` is `"off"`, and the
metrics test below fakes the engine and patches `run_deterministic_checks`
rather than touching Postgres — the isolation itself (a raising check does
not drop the report) is proved with a real database in
`tests/infra/test_assess_dq.py`."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import nora.app.assess_run as assess_run
from nora.app.assess_run import AssessDisabledError, render, run_nightly
from nora.domain.assess import AssessReport, DqCheckResult
from nora.obs.metrics import Metrics


async def test_run_nightly_refuses_when_mode_is_off():
    with pytest.raises(AssessDisabledError):
        await run_nightly(engine=object(), mode="off")  # type: ignore[arg-type]


def test_render_marks_ok_fail_and_not_applicable():
    report = AssessReport(
        kind="nightly_dq", run_at_ms=1234,
        results=(
            DqCheckResult(check="cross_room_duplicate_events", status="ok"),
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
    assert any("[OK] cross_room_duplicate_events" in line for line in lines)
    assert any(
        "[FAIL] empty_lineage_rows (3): a, b" in line
        and "expected before step 3 writers" in line
        for line in lines
    )
    assert any("[N/A] sections_past_age_horizon" in line for line in lines)


def test_render_marks_error():
    report = AssessReport(
        kind="nightly_dq", run_at_ms=1234,
        results=(
            DqCheckResult(
                check="cross_room_duplicate_events", status="error",
                detail="RuntimeError: boom",
            ),
        ),
    )
    lines = render(report)
    assert any(
        "[ERROR] cross_room_duplicate_events" in line and "RuntimeError: boom" in line
        for line in lines
    )


class _FakeEngine:
    """`run_nightly` only calls `.connect()` as an async context manager and
    hands the connection straight to `run_deterministic_checks`, which this
    test patches out — so the "connection" itself is never touched."""

    def connect(self):
        @asynccontextmanager
        async def _cm():
            yield object()

        return _cm()


async def test_run_nightly_counts_fail_and_error_on_separate_metrics(monkeypatch):
    """`nora_dq_failures_total` and `nora_dq_errors_total`
    must be two distinct series — a check that raised is not the same fact
    as a check that ran clean and found a violation."""
    results = (
        DqCheckResult(check="cross_room_duplicate_events", status="ok"),
        DqCheckResult(
            check="cross_room_duplicate_events", status="fail", failing=1,
            sample=("a/b",),
        ),
        DqCheckResult(
            check="happened_place_mismatch", status="error",
            detail="ValueError: boom",
        ),
    )

    async def _fake_checks(conn, *, rollup_builds=None, rollup_window_ms=(0, 0)):
        return results

    monkeypatch.setattr(assess_run, "run_deterministic_checks", _fake_checks)
    metrics = Metrics()

    outcome = await run_nightly(
        _FakeEngine(), mode="on", record=False, metrics=metrics,
    )

    assert outcome.report.failing_checks == ("cross_room_duplicate_events",)
    assert outcome.report.errored_checks == ("happened_place_mismatch",)
    assert metrics.get(
        "nora_dq_failures_total", check="cross_room_duplicate_events",
    ) == 1.0
    assert metrics.get(
        "nora_dq_errors_total", check="happened_place_mismatch",
    ) == 1.0
    # Neither series bleeds into the other.
    assert metrics.get(
        "nora_dq_errors_total", check="cross_room_duplicate_events",
    ) == 0.0
    assert metrics.get(
        "nora_dq_failures_total", check="happened_place_mismatch",
    ) == 0.0
