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


#: Constructors whose result is per-process STATE rather than a value.
#: Constant lookup tables are not here on purpose: a `dict` literal that is
#: only ever read is a constant, and flagging every one of them buries the
#: rule in ~80 false positives (measured). `re.compile`, `Path` and frozen
#: dataclasses are values too.
_STATEFUL_CONSTRUCTORS = frozenset({
    "defaultdict", "Counter", "deque", "OrderedDict",
    "Lock", "RLock", "Semaphore", "Event", "Condition", "Queue",
    "Formatter", "Handler", "TTLCache", "LRUCache",
})

#: Module-level names this rule deliberately allows, with the reason.
_ALLOWED = frozenset({
    # A logger IS the module-level object the stdlib intends; `getLogger` is
    # idempotent and returns the same instance to every caller.
    "log", "logger",
})


def _mutated_names(tree: ast.Module) -> set[str]:
    """Module-level names this file writes to after binding them — a subscript
    assignment, or a call to a mutating method on them."""
    writes = {"append", "add", "extend", "update", "setdefault", "pop", "clear"}
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    out.add(t.value.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in writes
            and isinstance(node.func.value, ast.Name)
        ):
            out.add(node.func.value.id)
    return out


def _is_mutable_construction(value: ast.Call) -> bool:
    """Whether this call builds per-process mutable state, whatever module the
    callable came from — `dict()`, `collections.defaultdict(...)`,
    `logging.Formatter(...)`."""
    name = getattr(value.func, "attr", None) or getattr(value.func, "id", None)
    return name in _STATEFUL_CONSTRUCTORS


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
            # A LOCALLY-DEFINED class was the only shape this caught, so both
            # of DEC-13's other forms walked straight through: a bare mutable
            # container (`_HANDLER_CACHE: dict = {}`) and an instance of an
            # IMPORTED class (`_SHARED_FORMATTER = logging.Formatter(...)`).
            # Both were added to `nora/obs/logging.py` and this file stayed
            # green (verified 2026-09-09). What the rule is about is
            # module-level state that outlives a request, not where the class
            # happens to be written.
            kind: str | None = None
            if isinstance(value, ast.Call) and (
                (isinstance(value.func, ast.Name) and value.func.id in local_classes)
                or _is_mutable_construction(value)
            ):
                kind = ast.unparse(value.func) + "(...)"
            elif (
                isinstance(value, ast.Dict | ast.List | ast.Set)
                and any(
                    isinstance(t, ast.Name) and t.id in _mutated_names(tree)
                    for t in targets
                )
            ):
                # A container that is WRITTEN somewhere in this module. A
                # read-only table is a constant and is left alone; the shape
                # DEC-13 forbids is state that accumulates across requests.
                kind = type(value).__name__.lower() + " literal, mutated in this module"
            if kind is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in _ALLOWED:
                    rel = path.relative_to(ROOT)
                    offenders.append(f"{rel}:{node.lineno}: {target.id} = {kind}")
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
