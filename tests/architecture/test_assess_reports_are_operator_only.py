"""Assess REPORTS ONLY (DEC-240): it writes `assess_report` and nothing else, and nothing on
a turn's path reads it back. Pinned by import graph: no tool, prefill or engagement module
in `nora/` imports `nora.infra.sql.assess`, `nora.app.assess_dq` or `nora.app.assess_run`
— the whole surface those modules expose stays reachable only from the CLI command that
runs them and this repo's tests."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASSESS_MODULES = ("nora.infra.sql.assess", "nora.app.assess_dq", "nora.app.assess_run")
#: The only files allowed to import an Assess module: the module itself, the
#: CLI command that runs it, and this repo's own tests.
_ALLOWED = {
    "nora/app/cli.py", "nora/infra/sql/assess.py", "nora/app/assess_dq.py",
    "nora/app/assess_run.py", "nora/obs/metric_specs.py",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_no_turn_facing_module_imports_an_assess_module():
    offenders = []
    for path in (_REPO_ROOT / "nora").rglob("*.py"):
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _ALLOWED:
            continue
        imported = _imports(path) & set(_ASSESS_MODULES)
        if imported:
            offenders.append((rel, sorted(imported)))
    assert offenders == []
