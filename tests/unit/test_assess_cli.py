"""`nora assess run-nightly`'s exit code (DEC-240; `nora.app.cli.cmd_assess_run_nightly`).
Everything below `_config` and `assess_run_nightly` is faked (no config file, no
database) because the one fact under test is the exit-code arithmetic: nonzero
when ANY check came back `fail` OR `error`.
"""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import nora.app.cli as cli
from nora.app.assess_run import NightlyReport
from nora.domain.assess import AssessReport, DqCheckResult


class _FakeEngine:
    async def dispose(self) -> None:
        pass


async def _run(monkeypatch, results: tuple[DqCheckResult, ...]) -> int:
    monkeypatch.setattr(
        cli, "_config", lambda args: SimpleNamespace(assess=SimpleNamespace(mode="on")),
    )
    monkeypatch.setattr(cli, "_database_url", lambda cfg: "postgresql://unused")
    monkeypatch.setattr(cli, "create_sql_engine", lambda url: _FakeEngine())

    async def _fake_run_nightly(engine, *, mode, record):
        report = AssessReport(kind="nightly_dq", run_at_ms=1, results=results)
        return NightlyReport(report=report, recorded=record)

    monkeypatch.setattr(cli, "assess_run_nightly", _fake_run_nightly)
    args = Namespace(config="unused.toml", no_record=False)
    return await cli.cmd_assess_run_nightly(args)


async def test_exit_code_is_zero_when_every_check_is_ok_or_not_applicable(monkeypatch):
    rc = await _run(monkeypatch, (
        DqCheckResult(check="cross_room_duplicate_events", status="ok"),
        DqCheckResult(check="sections_past_age_horizon", status="not_applicable"),
    ))
    assert rc == 0


async def test_exit_code_is_nonzero_on_a_fail(monkeypatch):
    rc = await _run(monkeypatch, (
        DqCheckResult(check="cross_room_duplicate_events", status="ok"),
        DqCheckResult(check="empty_lineage_rows", status="fail", failing=1, sample=("a",)),
    ))
    assert rc != 0


async def test_exit_code_is_nonzero_on_an_error_with_no_fail(monkeypatch):
    """A check that raised (status=error) makes the exit code nonzero exactly like
    a check that ran clean and found a violation: an operator scripting on this
    exit code must not see an errored check as an all-clear night."""
    rc = await _run(monkeypatch, (
        DqCheckResult(check="cross_room_duplicate_events", status="ok"),
        DqCheckResult(
            check="happened_place_mismatch", status="error",
            detail="RuntimeError: boom",
        ),
    ))
    assert rc != 0
