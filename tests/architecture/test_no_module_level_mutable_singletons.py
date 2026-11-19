"""DEC-13: CLAUDE.md's test-seam policy bans module-level mutable singletons — "production
classes take collaborators through `__init__` only ... no module-level mutable
singletons."

Two AST checks, one per shape of the pattern:

1. A module-level name bound to a call that constructs a class DEFINED in that same module
   (`METRICS = Metrics()`, right below `class Metrics:`). A plain grep for `^METRICS =`
   would only catch this by knowing the exact name in advance; walking the AST for
   "top-level assignment whose value calls a locally-defined class" catches the *shape*,
   so a differently-named reintroduction (or a new one entirely — `class Foo: ...` /
   `FOO = Foo()` at module scope anywhere under `nora/`) is caught too.
2. A `global` statement anywhere under `nora/` — the mechanism for rebinding a
   module-level name from inside a function (a client held in a module global and
   reassigned by `create_client`/`close_client`). `global` has no other legitimate use in
   this codebase, so its mere presence is the signal, not a specific name.
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
#: rule in false positives. `re.compile`, `Path` and frozen dataclasses are
#: values too.
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

#: (module, name) -> the exact `ast.unparse` text of the class definition
#: and of the assignment the exemption covers, verbatim.
_PINNED_SENTINELS = {
    ("nora/subject/obtain.py", "UNRESOLVED"): (
        "class _Unresolved:\n"
        '    """A config path the schema cannot give a concrete default for."""\n'
        "\n"
        "    def __repr__(self) -> str:\n"
        "        return 'UNRESOLVED'",
        "UNRESOLVED = _Unresolved()",
    ),
}

#: No shape analysis: any edit to the pinned source — a stateful dunder
#: body, an added base, a renamed method — changes the unparsed text and the
#: normal rule flags the binding again, forcing a deliberate re-pin.


def _matches_pinned_sentinel(
    tree: ast.Module, node: ast.Assign | ast.AnnAssign, rel: str, name: str
) -> bool:
    """The binding is exactly the pinned source, in the pinned module."""
    pin = _PINNED_SENTINELS.get((rel, name))
    if pin is None or ast.unparse(node) != pin[1]:
        return False
    return any(
        isinstance(top, ast.ClassDef) and ast.unparse(top) == pin[0]
        for top in tree.body
    )


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


def _singleton_offenders(tree: ast.Module, rel: str) -> list[str]:
    """One module's names bound to per-process state; synthetic-checkable."""
    local_classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    mutated = _mutated_names(tree)
    offenders: list[str] = []
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        # A LOCALLY-DEFINED class is not the only shape: a bare mutable container
        # (`_HANDLER_CACHE: dict = {}`) and an instance of an IMPORTED class
        # (`_SHARED_FORMATTER = logging.Formatter(...)`) are module-level state too.
        # What the rule is about is module-level state that outlives a request, not
        # where the class happens to be written.
        kind: str | None = None
        if isinstance(value, ast.Call) and (
            (isinstance(value.func, ast.Name) and value.func.id in local_classes)
            or _is_mutable_construction(value)
        ):
            kind = ast.unparse(value.func) + "(...)"
        elif (
            isinstance(value, ast.Dict | ast.List | ast.Set)
            and any(
                isinstance(t, ast.Name) and t.id in mutated
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
            if not isinstance(target, ast.Name) or target.id in _ALLOWED:
                continue
            if _matches_pinned_sentinel(tree, node, rel, target.id):
                continue
            offenders.append(f"{rel}:{node.lineno}: {target.id} = {kind}")
    return offenders


def test_no_module_level_instantiation_of_a_locally_defined_class() -> None:
    offenders: list[str] = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            _singleton_offenders(tree, path.relative_to(ROOT).as_posix()))
    assert not offenders, (
        "module-level singleton(s) — a class instantiated and bound to a "
        f"module-level name in its own module (DEC-13): {offenders}"
    )


def test_the_sentinel_pin_is_scoped_to_exact_source() -> None:
    """The exemption admits only `obtain.py`'s UNRESOLVED, and only while
    both the class and the assignment unparse to the pinned source — every
    edit, and the same binding in any other module, is flagged."""
    rel = "nora/subject/obtain.py"
    real = ast.parse(
        (ROOT / "nora/subject/obtain.py").read_text(encoding="utf-8"))
    assert _singleton_offenders(real, rel) == []

    sentinel = (
        "class _Unresolved:\n"
        '    """A config path the schema cannot give a concrete default for."""\n'
        "\n"
        "    def __repr__(self) -> str:\n"
        "        return 'UNRESOLVED'\n"
        "UNRESOLVED = _Unresolved()\n"
    )
    assert _singleton_offenders(ast.parse(sentinel), "nora/scratch.py")

    stateful_repr = (
        "class _Unresolved:\n"
        "    def __repr__(self) -> str:\n"
        "        self.items = []\n"
        "        return 'UNRESOLVED'\n"
        "UNRESOLVED = _Unresolved()\n"
    )
    assert _singleton_offenders(ast.parse(stateful_repr), rel)

    added_base = (
        "class Base:\n"
        "    pass\n"
        "class _Unresolved(Base):\n"
        "    def __repr__(self) -> str:\n"
        "        return 'UNRESOLVED'\n"
        "UNRESOLVED = _Unresolved()\n"
    )
    assert _singleton_offenders(ast.parse(added_base), rel)

    extra_method = (
        "class _Unresolved:\n"
        "    def __repr__(self) -> str:\n"
        "        return 'UNRESOLVED'\n"
        "    def reset(self):\n"
        "        pass\n"
        "UNRESOLVED = _Unresolved()\n"
    )
    assert _singleton_offenders(ast.parse(extra_method), rel)

    renamed = (
        "class _Unres:\n"
        "    def __repr__(self) -> str:\n"
        "        return 'UNRESOLVED'\n"
        "UNRESOLVED = _Unres()\n"
    )
    assert _singleton_offenders(ast.parse(renamed), rel)

    mutated_here = ast.parse(
        "UNRESOLVED = []\n"
        "def f():\n"
        "    UNRESOLVED.append(1)\n"
    )
    assert _singleton_offenders(mutated_here, rel)


def test_no_global_statements_in_production_code() -> None:
    """`global` is how a module-level name gets mutated from inside a function — the only
    legitimate reason to reach for it is rebinding a module-level name from a nested
    scope, which is the mechanism this rule exists to forbid. Any occurrence is exactly
    the pattern to reject on sight."""
    offenders: list[str] = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{node.lineno}: global {', '.join(node.names)}")
    assert not offenders, f"`global` statement(s) found (DEC-13): {offenders}"
