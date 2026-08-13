"""DEC-13: CLAUDE.md's test-seam policy bans module-level mutable singletons —
"production classes take collaborators through `__init__` only ... no
module-level mutable singletons." Phase 6 Task 4 deleted the last two:
`nora/obs/metrics.py`'s `METRICS = Metrics()` and `nora/agent/factory.py`'s
`_CLIENT` (reassigned via `global` inside `create_client`/`close_client`).

Two AST checks, one per shape of the pattern that actually occurred here:

1. A module-level name bound to a call that constructs a class DEFINED in
   that same module (`METRICS = Metrics()`, right below `class Metrics:`).
   This is the shape a plain grep for `^METRICS =` would only catch by
   knowing the exact name in advance; walking the AST for "top-level
   assignment whose value calls a locally-defined class" catches the *shape*
   instead, so a differently-named reintroduction (or a new one entirely —
   `class Foo: ...` / `FOO = Foo()` at module scope anywhere under `nora/`)
   is caught too, not just this one name.
2. A `global` statement anywhere under `nora/` — the mechanism `_CLIENT` used
   to rebind a module-level name from inside a function
   (`create_client`/`close_client`'s `global _CLIENT`). `global` has no other
   legitimate use in this codebase (confirmed: zero occurrences after Task
   4's rewrite), so its mere presence is the signal, not a specific name.

PROBE (see notes-tasks-3-4.md for the verbatim run): temporarily restored
`METRICS = Metrics()` at the bottom of `nora/obs/metrics.py` — check 1 above
failed with `nora/obs/metrics.py:xxx: METRICS = Metrics()`, confirming this
test is the one that would have caught DEC-13's deferred singletons if they'd
resurfaced. Restored immediately after.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _production_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py") if ".venv" not in p.parts
    )


def test_no_module_level_instantiation_of_a_locally_defined_class() -> None:
    offenders: list[str] = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        local_classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for node in tree.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in local_classes
            ):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    rel = path.relative_to(ROOT)
                    offenders.append(f"{rel}:{node.lineno}: {target.id} = {value.func.id}(...)")
    assert not offenders, (
        "module-level singleton(s) — a class instantiated and bound to a "
        f"module-level name in its own module (DEC-13): {offenders}"
    )


def test_no_global_statements_in_production_code() -> None:
    """`global` is how `_CLIENT` used to get mutated from inside a function —
    the only legitimate reason to reach for it is rebinding a module-level
    name from a nested scope, which is the mechanism this rule exists to
    forbid. Zero occurrences today; any new one is exactly the pattern to
    reject on sight."""
    offenders: list[str] = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{node.lineno}: global {', '.join(node.names)}")
    assert not offenders, f"`global` statement(s) found (DEC-13): {offenders}"
