"""«The wake evaluator in shadow fires nothing» as a property of the source.

The evaluator records would-wakes. For it to START anything — a Cognition
session, a Dreamer adaptor, a turn, a send — it would first have to import
something that can: the fire path, an adapter, the agent, the Dreamer
runtime, the engagement pipeline. So the module's `nora` imports are pinned
to the two it needs: the stimulus port and the pure wake policy. Adding any
other is a design change that must come with the step that makes the
evaluator live, and this test is where that change is noticed.

The behavioural half — a tick writes one shadow wake row and nothing else —
is `tests/unit/test_wake_evaluator.py::test_a_shadow_tick_writes_a_would_wake_and_nothing_else`.
"""

from __future__ import annotations

import ast
from pathlib import Path

WAKE = Path(__file__).resolve().parents[2] / "nora" / "app" / "wake.py"


def test_the_evaluator_imports_nothing_that_acts():
    tree = ast.parse(WAKE.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    nora_imports = {m for m in imported if m.startswith("nora")}
    assert nora_imports == {"nora.domain.ports", "nora.domain.stimuli"}, nora_imports
