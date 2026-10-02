"""`nora cognition health`'s command plumbing (the Cognition cut-over's PR-7,
`nora.app.cli.cmd_cognition_health`): the window, the exit code a check script
`&&`s on (0 PASS, 1 FAIL, 2 ERROR) and the `--json` form. Everything below `_config`
and `cognition_run_health` is faked, as `test_cognition_compare_cli.py` does; the reads
and the verdict are `tests/infra/test_cognition_health_read.py`, on real Postgres."""

from __future__ import annotations

import argparse
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

import nora.app.cli as cli
from nora.domain.cognition_health import (
    HealthReport,
    SessionCount,
    UnitState,
)


class _FakeEngine:
    async def dispose(self) -> None:
        pass


def _report(kind: str) -> HealthReport:
    base = {"since_ms": 0, "until_ms": 1000, "now_ms": 1000, "unit": None}
    day = SessionCount(day="2026-10-02", unit="alpha", outcome="closed", cause="none", count=8)
    if kind == "pass":
        return HealthReport(**base, sessions=(day,), units=(UnitState(
            unit="alpha", eligible=0, waiting=0, oldest_eligible_age_seconds=None,
        ),))
    if kind == "fail":
        return HealthReport(**base, sessions=(day,), units=(UnitState(
            unit="alpha", eligible=1, waiting=0, oldest_eligible_age_seconds=20_000.0,
        ),))
    return HealthReport(**base, sessions=(day,), errors=(("refusals", "ProgrammingError"),))


async def _run(monkeypatch, capsys, *, kind="pass", since=None, until=None, as_json=False,
               unit=None):
    seen: dict = {}
    monkeypatch.setattr(cli, "_config", lambda args: SimpleNamespace(
        cognition=SimpleNamespace(timezone="Asia/Hong_Kong", session_timeout_seconds=3900.0),
    ))
    monkeypatch.setattr(cli, "_database_url", lambda cfg: "postgresql://unused")
    monkeypatch.setattr(cli, "create_sql_engine", lambda url: _FakeEngine())

    async def _fake(engine, **kwargs):
        seen.update(kwargs)
        return _report(kind)

    monkeypatch.setattr(cli, "cognition_run_health", _fake)
    args = Namespace(config="unused.toml", since=since, until=until, unit=unit, json=as_json)
    rc = await cli.cmd_cognition_health(args)
    return rc, capsys.readouterr().out, seen


@pytest.mark.parametrize(("kind", "code", "verdict"), [
    ("pass", 0, "PASS"), ("fail", 1, "FAIL"), ("error", 2, "ERROR"),
])
async def test_the_exit_code_is_the_verdict_a_check_script_can_and_on(
    monkeypatch, capsys, kind, code, verdict,
):
    rc, out, _ = await _run(monkeypatch, capsys, kind=kind)
    assert rc == code
    assert f"verdict: {verdict}" in out


async def test_a_failure_says_which_threshold_and_an_error_says_which_section(
    monkeypatch, capsys,
):
    _, out, _ = await _run(monkeypatch, capsys, kind="fail")
    assert "FAIL stalled" in out and "5.6 h old" in out
    _, out, _ = await _run(monkeypatch, capsys, kind="error")
    assert "ERROR unreadable: refusals could not be read (ProgrammingError)" in out


async def test_json_is_one_parseable_object_with_the_verdict_and_the_exit_code(
    monkeypatch, capsys,
):
    rc, out, _ = await _run(monkeypatch, capsys, kind="fail", as_json=True)
    data = json.loads(out)
    assert data["schema"] == 1 and data["verdict"] == "FAIL" and data["exit_code"] == rc == 1
    assert data["findings"][0]["code"] == "stalled"
    assert data["sessions"][0] == {
        "day": "2026-10-02", "unit": "alpha", "outcome": "closed", "cause": "none", "count": 8,
    }


async def test_the_window_defaults_to_the_last_24_hours_and_the_unit_reaches_the_read(
    monkeypatch, capsys,
):
    _, _, seen = await _run(monkeypatch, capsys, unit="alpha")
    assert seen["until_ms"] - seen["since_ms"] == 24 * 3_600_000
    assert seen["unit"] == "alpha" and seen["session_timeout_seconds"] == 3900.0
    _, _, seen = await _run(
        monkeypatch, capsys, since="2026-10-01T00:00:00+08:00", until="2026-10-02T00:00:00+08:00",
    )
    assert seen["until_ms"] - seen["since_ms"] == 24 * 3_600_000


@pytest.mark.parametrize(("since", "until"), [
    ("2026-10-01T00:00:00", None),                                    # naive
    ("2026-10-02T00:00:00+08:00", "2026-10-01T00:00:00+08:00"),       # until before since
    ("not-a-date", None),                                             # unparseable
])
async def test_a_bad_window_is_an_error_not_a_failed_loop(monkeypatch, capsys, since, until):
    """Exit 1 means ONE thing: a measured threshold was breached. A window the command
    could not read is no measurement, so it is 2 (ERROR), the same code argparse gives a
    bad flag; a check script that reads 1 as «the loop is failing» must not read a typo
    as one."""
    rc, out, seen = await _run(monkeypatch, capsys, since=since, until=until)
    assert rc == 2 and out == "" and seen == {}             # no read ran, nothing printed


async def test_an_unreadable_config_is_an_error_not_a_failed_loop(tmp_path, capsys):
    """Unmocked: the real `_config` on a path that is not there (the review's probe:
    `--config /nonexistent.toml` exited 1)."""
    args = Namespace(
        config=str(tmp_path / "nonexistent.toml"), since=None, until=None, unit=None,
        json=False,
    )
    assert await cli.cmd_cognition_health(args) == 2
    assert "error:" in capsys.readouterr().err


async def test_the_shared_executor_gives_the_same_two_for_a_config_error(tmp_path, capsys):
    """`execute` is where every other command's CliError becomes 1; this one must come
    back from it as 2, not be rewritten."""
    args = Namespace(
        config=str(tmp_path / "nonexistent.toml"), since=None, until=None, unit=None,
        json=False, func=cli.cmd_cognition_health,
    )
    assert await cli.execute(args) == 2


@pytest.mark.parametrize("raised", [
    RuntimeError("boom"), ConnectionRefusedError("postgresql://u:SECRET@db/x"), OSError("disk"),
])
async def test_a_database_or_any_other_exception_is_an_error_and_leaks_no_message(
    monkeypatch, capsys, raised,
):
    monkeypatch.setattr(cli, "_config", lambda args: SimpleNamespace(
        cognition=SimpleNamespace(timezone="Asia/Hong_Kong", session_timeout_seconds=3900.0),
    ))
    monkeypatch.setattr(cli, "_database_url", lambda cfg: "postgresql://unused")
    monkeypatch.setattr(cli, "create_sql_engine", lambda url: _FakeEngine())

    async def _boom(engine, **kwargs):
        raise raised

    monkeypatch.setattr(cli, "cognition_run_health", _boom)
    rc = await cli.cmd_cognition_health(
        Namespace(config="unused.toml", since=None, until=None, unit=None, json=False),
    )
    captured = capsys.readouterr()
    assert rc == 2 and captured.out == ""
    assert type(raised).__name__ in captured.err and "SECRET" not in captured.err
    assert "no verdict" in captured.err


async def test_the_inert_kinds_follow_the_agenda_table_the_day_a_time_skill_ships(
    monkeypatch, capsys,
):
    """ONE table decides which kinds are inert (`nora.domain.stimuli.AGENDA_SKILL_KINDS`),
    and the command reads it at call time: hard-coding the answer here would keep
    reporting a floor as unclaimable after a `time` skill shipped. The control is the
    shipped (empty) table, which is the test below."""
    import nora.domain.stimuli as stimuli

    monkeypatch.setattr(stimuli, "AGENDA_SKILL_KINDS", {"time": "daily-floor"})
    _, _, seen = await _run(monkeypatch, capsys)
    assert seen["inert_kinds"] == frozenset()


@pytest.mark.parametrize(("flag", "since", "until"), [
    ("--since", "notadate", None), ("--until", None, "notadate"),
    ("--since", "2026-10-01T00:00:00", None),                        # naive: the tz message
])
async def test_a_bad_window_names_the_flag_that_was_bad(monkeypatch, capsys, flag, since, until):
    """`_cutover` said `--before` for every caller."""
    monkeypatch.setattr(cli, "_config", lambda args: SimpleNamespace(
        cognition=SimpleNamespace(timezone="Asia/Hong_Kong", session_timeout_seconds=3900.0),
    ))
    rc = await cli.cmd_cognition_health(
        Namespace(config="unused.toml", since=since, until=until, unit=None, json=False),
    )
    err = capsys.readouterr().err
    assert rc == 2 and f"error: {flag} " in err and "--before" not in err


async def test_the_inert_kinds_reach_the_read(monkeypatch, capsys):
    """Production ships no skill for `time`, so the command reads a `time` stimulus as
    what the evaluator reads it as: one no wake can claim."""
    _, _, seen = await _run(monkeypatch, capsys)
    assert seen["inert_kinds"] == {"time"}


def test_the_subcommand_is_registered_with_a_since_that_may_be_left_out():
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", default="x")
    top = argparse.ArgumentParser()
    cli.add_subcommands(top.add_subparsers(dest="cmd"), parent)
    args = top.parse_args(["cognition", "health"])
    assert args.func is cli.cmd_cognition_health
    assert (args.since, args.until, args.unit, args.json) == (None, None, None, False)
