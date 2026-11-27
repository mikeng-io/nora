"""Offline caller seams: deployment config and fail-closed report status."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from evals.cognition_loop.cases import generated_case, write_case

NOW = 1_800_000_000_000



@pytest.fixture(autouse=True)
def private_lock(tmp_path, monkeypatch):
    from nora.subject import sandbox

    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")


async def test_the_replay_refuses_a_version2_behavior_case(tmp_path):
    """The recorded replay measures version-1 state claims only: a
    turn-anchored behavior case is refused at the door, never silently
    measured by the wrong instrument."""
    from types import SimpleNamespace

    from evals.cognition_loop import run as run_mod

    case = SimpleNamespace(name="v2", unit="u", wakes=(), turns=("t",),
                           expectations=(), version=2)
    report = await run_mod.run_case(
        SimpleNamespace(), case, SimpleNamespace(), None,
        SimpleNamespace(), SimpleNamespace(), tmp_path)
    assert report["status"] == "ERROR"
    assert any("version-1" in e and "version-2" in e
               for e in report["errors"])


def test_run_materialises_the_config_and_folds_the_status(
    monkeypatch, tmp_path,
):
    import sys
    import types
    from contextlib import contextmanager
    from types import SimpleNamespace

    import evals.cognition_loop.__main__ as cli

    case_file = tmp_path / "case.json"
    write_case(generated_case(), case_file)
    config_file = tmp_path / "config.toml"
    config_file.write_text("x = 1\n")
    materialised: dict[str, Any] = {}

    def fake_materialise(**kwargs):
        materialised.update(kwargs)
        out = tmp_path / "config.evaluation.toml"
        out.write_text("x = 1\n")
        return out

    monkeypatch.setattr(cli, "materialise", fake_materialise)
    monkeypatch.setattr(cli, "load", lambda path: SimpleNamespace(
        store=SimpleNamespace(database_url_env="E"),
        honcho=SimpleNamespace(workspace_id="deploy-ws"),
    ))
    monkeypatch.setattr(cli, "snapshot_only", lambda *a, **k: None)
    monkeypatch.setattr(cli, "close_private_blank_templates", _async_noop())
    monkeypatch.setattr(cli, "private_directory", lambda p: p)
    monkeypatch.setattr(cli, "Judges", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "load_policy", lambda p, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "read_events", lambda p: [])
    monkeypatch.setattr(cli, "validate_events", lambda e: {"status": "PASS"})
    monkeypatch.setattr(
        cli, "create_sql_engine",
        lambda url: SimpleNamespace(dispose=_async_noop()))
    monkeypatch.setitem(
        sys.modules, "nora.secret",
        SimpleNamespace(credentials=lambda: {"E": "cred"}))
    backbone = types.ModuleType("evals.backbone")
    backbone._prefill_capture = lambda: None
    monkeypatch.setitem(sys.modules, "evals.backbone", backbone)

    @contextmanager
    def _noop_ctx(*a, **k):
        yield

    class _Evidence:
        def __init__(self, **kwargs):
            self.path = tmp_path / "events.jsonl"
            self.path.write_text("")

        capture = staticmethod(_noop_ctx)

        def record(self, *args, **kwargs):
            pass

    monkeypatch.setattr(cli, "RunEvidence", _Evidence)
    monkeypatch.setattr(cli, "measured_run_budget",
                        lambda *a, **k: _noop_ctx())
    async def failed_case(args, case, source, judges, policy, evidence, output):
        import json
        from dataclasses import asdict

        wakes = []
        for index, ident in enumerate(case.wakes):
            expectations = [{
                "expectation": {k: v for k, v in asdict(e).items() if k != "wake"},
                "verdict": "FAIL", "answers": [{"model": "a"}, {"model": "b"}],
            } for e in case.expectations if e.wake == index]
            key = {"id": (9000 + index) * 10 + 1}
            trajectory = {
                "unit": case.unit, "at_ms": 1000 + index,
                "wake_id": 9000 + index, "session_id": 7000 + index,
                "effects": {
                    "status": "observed", "unit": case.unit,
                    "items": [{
                        "ref": "wake:cognition_wake_outcome:" + json.dumps(
                            key, sort_keys=True, separators=(",", ":")),
                        "kind": "wake", "source": "cognition_wake_outcome",
                        "key": key, "at": "2026-01-01T00:00:00+00:00",
                        "operation": "append",
                        "row": {"id": key["id"], "wake_id": 9000 + index,
                                "unit": case.unit, "session_id": 7000 + index,
                                "outcome": "closed", "detail": "",
                                "at_ms": 1000 + index, "cause": "none"}}],
                    "tools": []}}
            wakes.append({"recorded_wake_id": ident, "status": "FAIL",
                          "wake_id": 9000 + index,
                          "session_id": 7000 + index,
                          "at_ms": 1000 + index,
                          "expectations": expectations,
                          "trajectory": trajectory})
            (output / f"wake-{index}-{ident}.json").write_text(json.dumps(
                {"diff": {}, "expectations": expectations,
                 "recorded_wake_id": ident, "unit": case.unit,
                 "at_ms": 1000 + index,
                 "trajectory": trajectory}))
        return {"case": case.name, "unit": case.unit, "status": "FAIL", "wakes": wakes}

    monkeypatch.setattr(cli, "run_case", failed_case)

    args = SimpleNamespace(
        case=case_file, case_dir=None, smoke=False,
        per_wake_tokens=1, token_budget=10_000_000, config=config_file,
        policy=None, identity=None, database_url="postgresql+asyncpg://x",
        snapshot=True, output=tmp_path, judge_a="a", judge_b="b",
        inspect_only=False, overrides={})
    report = asyncio.run(cli.run(args))
    assert materialised["deployment"] == config_file
    assert materialised["target"].name == "config.evaluation.toml"
    assert report["status"] == "FAIL"
    assert report["run"]["mode"] == "recorded-wake-protocol"
    assert report["run"]["behavior"] == "UNMEASURED"
    assert report["run"]["claim"] == "recorded-wake-replay"


def _async_noop():
    async def _noop(*a, **k):
        return None
    return _noop


def test_a_wakes_status_is_derived_fail_closed():
    from evals.cognition_loop.run import _wake_status

    assert _wake_status(("advance", "boom"), [{"verdict": "PASS"}]) == "ERROR"
    assert _wake_status(None, [{"verdict": "ERROR"}]) == "ERROR"
    assert _wake_status(
        None, [{"verdict": "FAIL"}, {"verdict": "REVIEW"}]) == "FAIL"
    assert _wake_status(None, [{"verdict": "REVIEW"}]) == "REVIEW"
    assert _wake_status(None, []) == "PASS"




async def test_an_unread_guild_is_an_error_naming_the_directory(
    monkeypatch, tmp_path,
):
    """A guild the unit expands through but the directory cannot answer for
    is ERROR at the room directory — never the silent "no policy room"."""
    from types import SimpleNamespace

    from evals.cognition_loop import run as run_mod

    async def _recorded(source, wake_id):
        return NOW, SimpleNamespace(unit="u", pending=())

    async def _reason(source, wake_id):
        return "time"

    async def _session(source, wake_id):
        return False

    monkeypatch.setattr(run_mod, "recorded_wake", _recorded)
    monkeypatch.setattr(run_mod, "_wake_reason", _reason)
    monkeypatch.setattr(run_mod, "_recorded_session", _session)
    policy = SimpleNamespace(
        conversations=[SimpleNamespace(unit="u", guild="g1")],
        rooms=lambda: [])
    args = SimpleNamespace(run_id="r", channel_directory=None,
                           service_inputs=SimpleNamespace(corpora={"cognition-loop": {"c": True}}))
    case = SimpleNamespace(name="c", unit="u", wakes=(1,), expectations=())
    report = await run_mod.run_case(
        args, case, SimpleNamespace(), None, policy,
        SimpleNamespace(), tmp_path)
    assert report["status"] == "ERROR"
    assert any("room directory" in e for e in report["errors"])


def test_a_corrupt_service_recording_still_freezes_the_report(
    monkeypatch, tmp_path,
):
    """A bad external recording leaves a named ERROR and both reports."""
    import sys
    import types
    from contextlib import contextmanager
    from types import SimpleNamespace

    import evals.cognition_loop.__main__ as cli

    case_file = tmp_path / "case.json"
    write_case(generated_case(), case_file)
    config_file = tmp_path / "config.toml"
    config_file.write_text("x = 1\n")
    corrupt = tmp_path / "frozen.json"
    corrupt.write_text("{ not json")

    monkeypatch.setattr(cli, "materialise", lambda **k: tmp_path / "g.toml")
    (tmp_path / "g.toml").write_text("x = 1\n")
    monkeypatch.setattr(cli, "load", lambda path: SimpleNamespace(
        store=SimpleNamespace(database_url_env="E"),
        honcho=SimpleNamespace(workspace_id="deploy-ws"),
    ))
    monkeypatch.setattr(cli, "snapshot_only", lambda *a, **k: None)
    monkeypatch.setattr(cli, "close_private_blank_templates", _async_noop())
    monkeypatch.setattr(cli, "private_directory", lambda p: p)
    monkeypatch.setattr(cli, "Judges", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "load_policy", lambda p, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "read_events", lambda p: [])
    monkeypatch.setattr(cli, "validate_events", lambda e: {"status": "PASS"})
    monkeypatch.setattr(
        cli, "create_sql_engine",
        lambda url: SimpleNamespace(dispose=_async_noop()))
    monkeypatch.setitem(
        sys.modules, "nora.secret",
        SimpleNamespace(credentials=lambda: {"E": "cred"}))
    backbone = types.ModuleType("evals.backbone")
    backbone._prefill_capture = lambda: None
    monkeypatch.setitem(sys.modules, "evals.backbone", backbone)

    @contextmanager
    def _noop_ctx(*a, **k):
        yield

    class _Evidence:
        def __init__(self, **kwargs):
            self.path = tmp_path / "events.jsonl"
            self.path.write_text("")

        capture = staticmethod(_noop_ctx)

        def record(self, *args, **kwargs):
            pass

    monkeypatch.setattr(cli, "RunEvidence", _Evidence)

    args = SimpleNamespace(
        case=case_file, case_dir=None, smoke=False,
        per_wake_tokens=1, token_budget=10_000_000,
        config=config_file, policy=None, identity=None,
        database_url="postgresql+asyncpg://x", snapshot=True,
        output=tmp_path, judge_a="a", judge_b="b",
        inspect_only=True, overrides={},
        services=corrupt,
    )
    report = asyncio.run(cli.run(args))
    assert report["status"] == "ERROR"
    assert report["errors"] and report["errors"][0]["step"] == "external_services"
    assert not report.get("cases")
    report_json = (tmp_path / "report.json")
    assert report_json.is_file()
    html_text = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "external_services" in html_text
    assert "ERROR" in html_text
