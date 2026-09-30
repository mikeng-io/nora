"""`nora cognition compare --since <ISO>` (migration step 3a-ii,
`nora.app.cli.cmd_cognition_compare`). Everything below `_config` and
`cognition_run_compare` is faked — no config file, no database — the one
fact under test is the CLI's own plumbing: window parsing, exit code, and
that it prints the counts `nora.app.cognition_compare.render` produces."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import nora.app.cli as cli
from nora.domain.cognition_compare import CompareResult, MirroredAct, ProactiveAct


class _FakeEngine:
    async def dispose(self) -> None:
        pass


async def _run(monkeypatch, capsys, *, since="2026-09-29T00:00:00+08:00", until=None):
    monkeypatch.setattr(cli, "_config", lambda args: SimpleNamespace())
    monkeypatch.setattr(cli, "_database_url", lambda cfg: "postgresql://unused")
    monkeypatch.setattr(cli, "create_sql_engine", lambda url: _FakeEngine())

    m = MirroredAct(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("note:n1",), at_ms=1000,
    )
    p = ProactiveAct(
        reminder_id="p1", platform="signal", conversation_id="room-a",
        note_id="note:n1", at_ms=1000,
    )

    async def _fake_run_compare(engine, window):
        return CompareResult(matched=((m, p),), cognition_only=(), proactive_only=())

    monkeypatch.setattr(cli, "cognition_run_compare", _fake_run_compare)
    args = Namespace(config="unused.toml", since=since, until=until)
    rc = await cli.cmd_cognition_compare(args)
    return rc, capsys.readouterr().out


async def test_exit_code_is_always_zero(monkeypatch, capsys):
    rc, _ = await _run(monkeypatch, capsys)
    assert rc == 0


async def test_prints_the_counts(monkeypatch, capsys):
    _, out = await _run(monkeypatch, capsys)
    assert "matched:         1" in out
    assert "cognition_only:  0" in out
    assert "proactive_only:  0" in out


async def test_a_naive_since_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_config", lambda args: SimpleNamespace())
    args = Namespace(config="unused.toml", since="2026-09-29T00:00:00", until=None)
    try:
        await cli.cmd_cognition_compare(args)
        raised = False
    except cli.CliError:
        raised = True
    assert raised
