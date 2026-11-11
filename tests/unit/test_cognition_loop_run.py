"""`scripts.cognition_loop.__main__.run` — the run-level seams: the
config under test is materialised from the deployment's `--config`, and
the final status folds the cases' own verdicts, never a collapsed
"done". Offline: every collaborator is monkeypatched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from scripts.cognition_loop.cases import generated_case, write_case


def test_run_materialises_the_config_and_folds_the_status(
    monkeypatch, tmp_path,
):
    import sys
    import types
    from contextlib import contextmanager
    from types import SimpleNamespace

    import scripts.cognition_loop.__main__ as cli

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
    monkeypatch.setattr(cli, "private_directory", lambda p: p)
    monkeypatch.setattr(cli, "Judges", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "evaluation_export_for", lambda p: None)
    monkeypatch.setattr(cli, "load_policy", lambda p: SimpleNamespace())
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

    monkeypatch.setattr(cli, "RunEvidence", _Evidence)
    monkeypatch.setattr(cli, "measured_run_budget",
                        lambda *a, **k: _noop_ctx())
    monkeypatch.setattr(cli, "run_case", _async_result({"status": "FAIL"}))

    args = SimpleNamespace(
        case=case_file, case_dir=None, smoke=False,
        per_wake_tokens=1, token_budget=10,
        config=config_file, policy=None, identity=None,
        database_url="postgresql+asyncpg://x", snapshot=True,
        output=tmp_path, judge_a="a", judge_b="b",
        inspect_only=False, overrides={},
    )
    report = asyncio.run(cli.run(args))
    # A different --config is a different subject: it is what gets
    # materialised into the config every case composes.
    assert materialised["deployment"] == config_file
    assert materialised["target"].name == "config.evaluation.toml"
    # The case's own verdict reaches the report unchanged — never "done".
    assert report["status"] == "FAIL"


def _async_noop():
    async def _noop(*a, **k):
        return None
    return _noop


def _async_result(value):
    async def _call(*a, **k):
        return value
    return _call
