"""`nora cognition compare` (migration step 3a, review B1/D5,
`nora.app.cli.cmd_cognition_compare`). Everything below `_config` and
`cognition_run_shadow_compare` is faked: no config file, no database. The facts
under test are the CLI's own plumbing: window parsing, the exit code (1 only
for an ERROR tier), the `--json` form, and that `--unit` names a real unit."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

import nora.app.cli as cli
from nora.domain.cognition_shadow_compare import (
    ShadowReport,
    TierResult,
    WriterVerdict,
)


class _FakeEngine:
    async def dispose(self) -> None:
        pass


def _report(*, error: bool = False) -> ShadowReport:
    tier = (
        TierResult.errored("note", "OperationalError") if error else TierResult(
            tier="note", status="compared",
            writers=(WriterVerdict(writer="note-candidates", status="compared", matched=14),),
        )
    )
    return ShadowReport(since_ms=0, until_ms=1000, tolerance_ms=60_000, unit=None, tiers=(tier,))


async def _run(monkeypatch, capsys, *, since="2026-09-29T00:00:00+08:00", until=None,
               as_json=False, error=False, unit=None, tolerance=360):
    seen: dict = {}
    monkeypatch.setattr(cli, "_config", lambda args: SimpleNamespace())
    monkeypatch.setattr(cli, "_database_url", lambda cfg: "postgresql://unused")
    monkeypatch.setattr(cli, "create_sql_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(cli, "_room_directory", lambda cfg, u: cli.RoomDirectory({}, {}))

    async def _fake(engine, window, *, tolerance_ms, unit, directory):
        seen.update(window=window, tolerance_ms=tolerance_ms, unit=unit)
        return _report(error=error)

    monkeypatch.setattr(cli, "cognition_run_shadow_compare", _fake)
    args = Namespace(
        config="unused.toml", since=since, until=until, unit=unit,
        tolerance_minutes=tolerance, json=as_json,
    )
    rc = await cli.cmd_cognition_compare(args)
    return rc, capsys.readouterr().out, seen


async def test_a_compared_report_exits_zero_and_prints_a_verdict_per_writer(monkeypatch, capsys):
    rc, out, _ = await _run(monkeypatch, capsys)
    assert rc == 0
    assert "note-candidates: 14 matched" in out


async def test_an_error_tier_exits_one_and_says_error_not_zero(monkeypatch, capsys):
    rc, out, _ = await _run(monkeypatch, capsys, error=True)
    assert rc == 1
    assert "note-candidates: ERROR (OperationalError)" in out
    assert "0 matched" not in out


async def test_json_is_one_parseable_object_with_the_verdicts(monkeypatch, capsys):
    rc, out, _ = await _run(monkeypatch, capsys, as_json=True)
    data = json.loads(out)
    assert rc == 0 and data["status"] == "compared"
    assert data["verdicts"][0].startswith("note-candidates: 14 matched")


async def test_the_tolerance_and_unit_reach_the_read(monkeypatch, capsys):
    _, _, seen = await _run(monkeypatch, capsys, unit="u", tolerance=90)
    assert seen["tolerance_ms"] == 90 * 60_000 and seen["unit"] == "u"


@pytest.mark.parametrize("since,until,tolerance", [
    ("2026-09-29T00:00:00", None, 360),                               # naive
    ("2026-09-29T00:00:00+08:00", "2026-09-28T00:00:00+08:00", 360),  # until before since
    ("2026-09-29T00:00:00+08:00", None, -1),                          # negative tolerance
])
async def test_a_bad_window_or_tolerance_is_refused(monkeypatch, capsys, since, until, tolerance):
    with pytest.raises(cli.CliError):
        await _run(monkeypatch, capsys, since=since, until=until, tolerance=tolerance)


def test_the_default_tolerance_is_the_derived_one_not_a_guess():
    import argparse

    from nora.domain.cognition_shadow_compare import DEFAULT_TOLERANCE_MINUTES

    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", default="x")
    top = argparse.ArgumentParser()
    cli.add_subcommands(top.add_subparsers(dest="cmd"), parent)
    args = top.parse_args(["cognition", "compare", "--since", "2026-09-29T00:00:00+08:00"])
    assert args.tolerance_minutes == DEFAULT_TOLERANCE_MINUTES == 65
