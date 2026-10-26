"""The archived local-model probe (`design/context-plan/evidence/local_models.py`)
writes its results somewhere only its own account can write. Its results used
to go to a fixed, predictable `/tmp` path, where another local account could
plant a link and have the probe overwrite its target. The results directory is
now made by `tempfile.mkdtemp` (0700), and no fixed `/tmp` path is assigned to
the results root. A source property, read from the AST so the probe is never
imported: it loads torch and models at import time.
"""

from __future__ import annotations

import ast
import json
import pathlib
import stat
import tempfile
from pathlib import Path

PROBE = (
    Path(__file__).resolve().parents[2] / "design" / "context-plan" / "evidence" / "local_models.py"
)


def _tree() -> ast.Module:
    return ast.parse(PROBE.read_text(encoding="utf-8"))


def test_results_are_written_inside_a_fresh_private_directory(tmp_path, monkeypatch) -> None:
    # Execute only the actual output allocation and write, without torch/models
    # or the archived /tmp input script. Both must use the same private root.
    tree = _tree()
    allocation = next(
        n for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in {"ROOT", "OUT"} for t in n.targets)
    )
    write = next(
        n for n in ast.walk(tree) if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "write_text"
    )
    mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: mkdtemp(dir=tmp_path, **kw))
    ns = {"pathlib": pathlib, "tempfile": tempfile, "json": json,
          "ns": {"CASES": [], "QUESTIONS": [], "DEFS": []},
          "kind": "sample", "load_seconds": 0, "rows": []}
    for _ in range(2):
        exec(compile(ast.Module(body=[allocation], type_ignores=[]), str(PROBE), "exec"), ns)  # noqa: S102
        root = ns.get("OUT", ns.get("ROOT"))
        assert root.parent == tmp_path, "the output must have a freshly allocated parent"
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        exec(compile(ast.Module(body=[write], type_ignores=[]), str(PROBE), "exec"), ns)  # noqa: S102
        assert json.loads((root / "sample-results.json").read_text())["model"] == "sample"
    assert len(list(tmp_path.iterdir())) == 2
