"""`scripts.cognition_loop.__main__.run` — the failure-outcome seams:
`report.json` and `report.html` exist after an evidence-raising run, the
stated budget prints before any model call, `--stop-after-tokens` is
refused at startup, and the per-wake default is the measured figure.
Offline: every collaborator is monkeypatched.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

from scripts.cognition_loop.cases import generated_case, write_case


def _async_noop():
    async def _noop(*a, **k):
        return None
    return _noop


def _async_result(value):
    async def _result(*a, **k):
        return value
    return _result


def _offline_run_setup(monkeypatch, tmp_path, *, capture=None):
    """The smallest slice of offline collaborators `cli.run` needs."""
    import sys
    import types
    from contextlib import contextmanager
    from types import SimpleNamespace

    import scripts.cognition_loop.__main__ as cli

    case_file = tmp_path / "case.json"
    write_case(generated_case(), case_file)
    config_file = tmp_path / "config.toml"
    config_file.write_text("x = 1\n")

    def fake_materialise(**kwargs):
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

    if capture is None:
        @contextmanager
        def capture(*a, **k):
            yield
    capture_fn = capture

    class _Evidence:
        def __init__(self, **kwargs):
            self.path = tmp_path / "events.jsonl"
            self.path.write_text("")

        capture = staticmethod(capture_fn)

    monkeypatch.setattr(cli, "RunEvidence", _Evidence)
    monkeypatch.setattr(cli, "measured_run_budget",
                        lambda *a, **k: _ctx())
    return cli, case_file, config_file


@contextmanager
def _ctx(*a, **k):
    yield


def _offline_args(case_file, config_file, tmp_path):
    from types import SimpleNamespace

    return SimpleNamespace(
        case=case_file, case_dir=None, smoke=False,
        per_wake_tokens=1, token_budget=1_000_000,
        config=config_file, policy=None, identity=None,
        database_url="postgresql+asyncpg://x", snapshot=True,
        output=tmp_path, judge_a="a", judge_b="b",
        inspect_only=True, overrides={},
        room_directory=None, channel_reads_url=None,
    )


def test_a_model_error_still_writes_report_json_and_html(
    monkeypatch, tmp_path,
):
    """A failed run freezes its report: `evidence.capture` raises
    `EvidenceError("model_error")` on exit, and report.json/report.html
    are still written — ERROR with the cause named."""
    import json as _json
    from contextlib import contextmanager

    import pytest

    from nora.subject.eval_evidence import EvidenceError

    @contextmanager
    def _model_error_capture(*a, **k):
        yield
        raise EvidenceError("model_error")

    cli, case_file, config_file = _offline_run_setup(
        monkeypatch, tmp_path, capture=_model_error_capture)
    monkeypatch.setattr(cli, "run_case", _async_result({"status": "PASS"}))

    with pytest.raises(EvidenceError, match="model_error"):
        asyncio.run(cli.run(_offline_args(case_file, config_file, tmp_path)))

    report = _json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "ERROR"
    assert any(e.get("message") == "model_error" for e in report["errors"])
    html_text = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "model_error" in html_text and "ERROR" in html_text


def test_a_report_html_failure_turns_a_passing_run_to_error(
    monkeypatch, tmp_path,
):
    """report.json stands when the HTML render raises, and the run is ERROR
    naming the step, never a PASS with no report."""
    import json as _json

    cli, case_file, config_file = _offline_run_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "run_case", _async_result({"status": "PASS"}))
    ok = tmp_path / "ok"
    ok.mkdir()
    assert asyncio.run(cli.run(_offline_args(case_file, config_file, ok)))[
        "status"] == "PASS"

    def _raise(output):
        raise RuntimeError("generated render failure")

    monkeypatch.setattr(cli.report_html, "render", _raise)
    failed = tmp_path / "failed"
    failed.mkdir()
    report = asyncio.run(cli.run(_offline_args(case_file, config_file, failed)))
    assert report["status"] == "ERROR"
    written = _json.loads((failed / "report.json").read_text())
    assert written["status"] == "ERROR"
    assert any(e.get("step") == "report_html" for e in written["errors"])


def test_the_stated_budget_counts_judges_and_is_printed_first(
    monkeypatch, tmp_path, capsys,
):
    """The stated budget is wakes x per-wake plus the judge allowance,
    printed before any model call."""
    import scripts.cognition_loop.__main__ as cli

    case = generated_case()  # 2 wakes, 2 expectations
    expected = 2 * 1 + len(case.expectations) * 2 * cli.JUDGE_TOKENS_PER_CALL
    assert cli.token_budget([case], 1) == expected

    cli, case_file, config_file = _offline_run_setup(
        monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "run_case", _async_result({"status": "PASS"}))
    asyncio.run(cli.run(_offline_args(case_file, config_file, tmp_path)))
    out = capsys.readouterr().out
    assert "stated budget" in out
    assert "2 wakes x" in out and f"{expected} tokens" in out


def test_stop_after_tokens_is_refused_at_startup(tmp_path, capsys):
    """A measured cutoff only works against sequential calls; the run's
    calls are parallel, so the flag is refused before anything starts."""
    import pytest

    import scripts.cognition_loop.__main__ as cli

    with pytest.raises(SystemExit):
        cli.main([
            "--config", str(tmp_path / "c.toml"),
            "--policy", str(tmp_path / "p.toml"),
            "--identity", str(tmp_path / "i.toml"),
            "--database-url", "postgresql+asyncpg://x",
            "--snapshot",
            "--case", str(tmp_path / "case.json"),
            "--output", str(tmp_path / "out"),
            "--stop-after-tokens", "5",
        ])
    assert "parallel" in capsys.readouterr().err


def test_the_default_per_wake_figure_is_the_measured_live_figure():
    import scripts.cognition_loop.__main__ as cli

    assert cli.DEFAULT_TOKENS_PER_WAKE == 800_000
