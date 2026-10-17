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
