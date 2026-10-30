"""`python -m evals`'s run budget, wired: the estimate is printed before any
draw, and a run over `BUDGET_MINUTES` is refused with rc 2 unless
`--allow-long` is passed. No DB, no network: every collaborator `main()`
calls that could reach Postgres or Honcho is replaced, as in
`test_eval_main_shared_db_guard`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import evals.__main__ as m
from evals.scenarios import selected_cases


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "301"])
def test_invalid_judge_timeout_is_refused_before_setup(monkeypatch, value, capsys):
    monkeypatch.setattr("sys.argv", ["python -m evals", "--judge-timeout", value, "--list"])
    with pytest.raises(SystemExit) as exc:
        m.main()
    assert exc.value.code == 2
    assert "judge timeout must be finite" in capsys.readouterr().err


def test_judge_timeout_reaches_existing_judge(monkeypatch, tmp_path):
    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    seen = []
    original = m.Judge

    def bounded_judge(**kwargs):
        seen.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(m, "Judge", bounded_judge)
    monkeypatch.setattr("sys.argv", ["python -m evals", "tool_selection", "--case",
                                     "route_where_is_now", "--judge", "--judge-timeout", "90"])
    assert m.main() == 0
    assert seen and seen[0]["timeout_seconds"] == 90


def test_spend_keeps_missing_usage_unmeasured(capsys):
    import evals.backbone as b

    b.drain_spend()
    b._SPEND.append({b.NORA_CALLS: 1, b.NORA_INPUT_TOKENS: 0,
                     b.NORA_OUTPUT_TOKENS: 0, b.NORA_CACHED_TOKENS: 0,
                     b.NORA_STOPS: "stop", "usage_known": False})
    m._print_spend()
    assert "usage UNMEASURED for 1 call(s)" in capsys.readouterr().out


def test_config_option_reaches_the_judge_endpoint(monkeypatch, tmp_path, example_config_path):
    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    config = tmp_path / "config.toml"
    config.write_text(example_config_path.read_text() + '''
[providers.judge]
base_url = "https://judge.invalid/v1"
api_key_env = "CUSTOM_JUDGE_KEY"
[model_routes.eval_judge]
provider = "judge"
model = "routed-judge"
''')
    monkeypatch.setenv("CUSTOM_JUDGE_KEY", "synthetic-judge-key")
    endpoints = []

    def run_one(*args, **kwargs):
        endpoints.append(kwargs["judge"]._endpoint())
        return True

    monkeypatch.setattr(m, "_run_one", run_one)
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--case", "route_where_is_now",
        "--judge", "--config", str(config),
    ])
    assert m.main() == 0
    assert endpoints == [("https://judge.invalid/v1", "synthetic-judge-key", "routed-judge")]


def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runs: list[str]) -> None:
    monkeypatch.setattr(m, "source_url_for", lambda *a, **k: "URL")
    monkeypatch.setattr(m, "refuse_if_source_diverges_from_head", lambda url: None)
    monkeypatch.setattr(m, "_sweep_stale_private_blanks", lambda url: None)
    monkeypatch.setattr(m, "shared_database_versions", lambda url: None)
    written = tmp_path / "config.evaluation.toml"
    written.write_text("", encoding="utf-8")  # the deployment's index off
    monkeypatch.setattr(m, "materialise", lambda **k: str(written))
    monkeypatch.setattr(m, "_sweep_stale_workspaces", lambda: None)
    monkeypatch.setattr(m, "graph_surface_lines", lambda *a: runs.append("graph") or [])
    monkeypatch.setattr(m, "embedder_model_of", lambda text: "m")
    monkeypatch.setattr(m, "_run_one", lambda *a, **k: runs.append("run") or True)

    async def _close() -> None:
        return None

    monkeypatch.setattr(m, "close_private_blank_templates", _close)
    monkeypatch.setattr(m, "assert_shared_databases_unchanged", lambda url, before: None)


def test_a_run_over_the_budget_is_refused_before_any_draw(monkeypatch, tmp_path, capsys):
    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    monkeypatch.setattr(
        "sys.argv", ["python -m evals", "tool_selection", "--memory-index", "--sample", "3"],
    )
    assert m.main() == 2
    cases = len(selected_cases("tool_selection", memory_index=True))
    out, err = capsys.readouterr()
    assert f"estimate: {cases} cases x 3 samples x 1 arm = {cases * 3} draws" in out
    assert "over the 60-minute budget" in err
    assert runs == []  # neither the graph checks nor a draw


def test_allow_long_lets_the_same_run_start(monkeypatch, tmp_path, capsys):
    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--memory-index", "--sample", "3", "--allow-long",
    ])
    assert m.main() == 0
    assert runs == ["graph", "run"]
    assert "estimate: " in capsys.readouterr().out


def test_a_narrow_run_starts_without_allow_long(monkeypatch, tmp_path, capsys):
    runs: list[str] = []
    _stub(monkeypatch, tmp_path, runs)
    [first, second] = [c.name for c in selected_cases("tool_selection", memory_index=True)][:2]
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection", "--memory-index", "--sample", "3",
        "--case", first, "--case", second,
    ])
    assert m.main() == 0
    assert runs == ["graph", "run"]
    assert "estimate: 2 cases x 3 samples x 1 arm = 6 draws, ~5 min" in capsys.readouterr().out
