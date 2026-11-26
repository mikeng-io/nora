"""No live writer projects the legacy memory index.

The projector and its tests stay while the legacy tables do: explicit replay,
recovery and bake-off substrate may still seed it, and deletion custody still
calls `drop`. What left in this layer is the production write-through: a
record, notebook, rollup, social or media write no longer writes a legacy
node, edge or graph-work row as a side effect.

This scan is import-resolution based, not a word grep. It sees module and
function-local imports, `from nora.infra.sql import memory_index`,
`from nora.infra.sql.memory_index import project`, aliases, and fully
qualified calls. Its positive controls prove both the scanner and the retained
exceptions; the zero they frame is the production-write ban. The write scan
keeps its own positive controls because it also guards the boundary that only
`memory_index.py` writes the memory tables.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from nora.infra.sql import schema

ROOT = Path(__file__).resolve().parents[2]
_MEMORY_INDEX_MODULE = "nora.infra.sql.memory_index"
_PROJECTOR = "nora/infra/sql/memory_index.py"
_PROJECTING_CALLS = frozenset({
    "project", "project_episodes", "reproject_episodes", "reproject_media", "rebuild",
})
_WRITE_FUNCS = frozenset({"insert", "pg_insert", "update", "delete"})
_RAW = re.compile(
    r'\bINSERT\s+INTO\s+"?([a-z_]+)'
    r'|\bUPDATE\s+"?([a-z_]+)"?\s+SET\b'
    r'|\bDELETE\s+FROM\s+"?([a-z_]+)',
    re.IGNORECASE,
)

_ALLOWED_PROJECTION_FILES = frozenset({
    "nora/infra/sql/memory_index.py",
    "nora/app/graph_project.py",
    "nora/infra/sql/profile.py",
    "nora/subject/lexical_seed.py",
})

#: The retained sandbox replay, reached through `scripts/cognition_loop/run.py`.
#: No other operator entry point may write the legacy index.
_SANDBOX_REPLAY_FILES = frozenset({"scripts/memory_graph_spike.py"})
_SCAN_ROOTS = ("nora", "scripts")


def _projector_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to the memory-index module or to one of its projection calls."""
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if f"{node.module}.{alias.name}" == _MEMORY_INDEX_MODULE:
                    modules.add(alias.asname or alias.name)
                elif node.module == _MEMORY_INDEX_MODULE and alias.name in _PROJECTING_CALLS:
                    functions.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _MEMORY_INDEX_MODULE:
                    modules.add(alias.asname or alias.name.rsplit(".", 1)[-1])
    return modules, functions


def _projection_call(node: ast.AST, modules: set[str], functions: set[str]) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id in functions:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _PROJECTING_CALLS:
        value = func.value
        if isinstance(value, ast.Name) and value.id in modules:
            return func.attr
        if ast.unparse(value) == _MEMORY_INDEX_MODULE:
            return func.attr
    return None


def _projection_calls(path: Path) -> list[tuple[str, int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, functions = _projector_names(tree)
    return [
        (str(path.relative_to(ROOT)), node.lineno, name)
        for node in ast.walk(tree)
        if (name := _projection_call(node, modules, functions)) is not None
    ]


def _production_projection_calls() -> list[tuple[str, int, str]]:
    found = []
    for root in _SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "migrations" in path.parts:
                continue
            found.extend(_projection_calls(path))
    return found


def _table_of(node: ast.expr, aliases: dict[str, str]) -> str | None:
    """A schema table named by a constant, `schema.CONSTANT`, or a local alias."""
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id]
        return getattr(getattr(schema, node.id, None), "name", None)
    if isinstance(node, ast.Attribute):
        return getattr(getattr(schema, node.attr, None), "name", None)
    return None


def _aliases(scope: ast.AST, inherited: dict[str, str]) -> dict[str, str]:
    """Simple `name = <table>` assignments in `scope` (not nested defs)."""
    out = dict(inherited)
    for node in _own_nodes(scope):
        if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            table = _table_of(node.value, out)
            if table is not None:
                out[node.targets[0].id] = table
    return out


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node in `scope`, not descending into a nested def."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(ast.iter_child_nodes(node))
    return out


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _docstrings(tree: ast.Module) -> set[int]:
    """The `id`s of every docstring's constant in `tree`."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _written_tables(node: ast.AST, aliases: dict[str, str] | None = None) -> set[str]:
    """The tables one AST node writes, by every form the scan recognises."""
    aliases = aliases or {}
    found: set[str] = set()
    if isinstance(node, ast.Call):
        name = _callee(node)
        if name in _WRITE_FUNCS and node.args:
            table = _table_of(node.args[0], aliases)
            if table is not None:
                found.add(table)
        if (
            name in {"insert", "update", "delete"} and not node.args
            and isinstance(node.func, ast.Attribute)
        ):
            table = _table_of(node.func.value, aliases)
            if table is not None:
                found.add(table)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        for groups in _RAW.findall(node.value):
            found.add(next(g for g in groups if g).lower())
    return found


def _scan(tree: ast.Module) -> list[tuple[str, str, bool]]:
    """Every write in one module: (enclosing function, table, projects?)."""
    modules, functions = _projector_names(tree)
    docstrings = _docstrings(tree)
    out: list[tuple[str, str, bool]] = []

    def visit(scope: ast.AST, name: str, inherited: dict[str, str]) -> None:
        aliases = _aliases(scope, inherited)
        projects = not isinstance(scope, ast.Module) and any(
            _projection_call(n, modules, functions) is not None for n in _own_nodes(scope)
        )
        for node in _own_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node, node.name, aliases)
            if id(node) in docstrings:
                continue
            for table in _written_tables(node, aliases):
                out.append((name, table, projects))

    visit(tree, "<module>", {})
    return out


def _writes() -> list[tuple[str, str, str, bool]]:
    """Every write in `nora/`: (path, enclosing function, table, projects?)."""
    out: list[tuple[str, str, str, bool]] = []
    for path in sorted((ROOT / "nora").rglob("*.py")):
        if "migrations" in path.parts:
            continue
        rel = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out.extend((rel, scope, table, projects) for scope, table, projects in _scan(tree))
    return out


def test_the_scan_sees_every_import_form() -> None:
    """Positive control: no import shape can make the production zero vacuous."""
    write = "def f():\n"
    cases = {
        "package module": "from nora.infra.sql import memory_index\n" + write
        + "    memory_index.project(conn, 'message', 1)\n",
        "module alias": "from nora.infra.sql import memory_index as mi\n" + write
        + "    mi.reproject_media(conn, 'x')\n",
        "function import": "from nora.infra.sql.memory_index import project as p\n" + write
        + "    p(conn, 'message', 1)\n",
        "function-local import": write
        + "    from nora.infra.sql import memory_index\n"
        + "    memory_index.project_episodes(conn, 1, 'stay', [])\n",
        "fully qualified": write
        + "    nora.infra.sql.memory_index.reproject_episodes(conn)\n",
        "direct module import": "import nora.infra.sql.memory_index\n" + write
        + "    memory_index.project(conn, 'message', 1)\n",
    }
    for label, code in cases.items():
        tree = ast.parse(code)
        modules, functions = _projector_names(tree)
        assert any(
            _projection_call(node, modules, functions)
            for node in ast.walk(tree)
        ), label


def test_the_scan_has_positive_controls_in_the_tree() -> None:
    """The retained substrate really does call the projector; an empty scan is red."""
    sites = {(path, name) for path, _, name in _production_projection_calls()}
    assert ("nora/subject/lexical_seed.py", "project") in sites
    assert ("nora/infra/sql/profile.py", "project") in sites
    assert ("nora/app/graph_project.py", "project") in sites
    assert ("nora/app/graph_project.py", "reproject_episodes") in sites
    assert ("scripts/memory_graph_spike.py", "rebuild") in sites


def test_no_live_writer_projects_the_legacy_index() -> None:
    """Operator entry points are in scope; only the named sandbox replay is excepted.

    Negative control: restoring `await memory_index.reproject_media(conn, sha256)`
    after a successful ingest in `scripts/ingest_self_asset.py` must turn this
    test red, because that script is outside `_SANDBOX_REPLAY_FILES`.
    """
    disallowed = sorted(
        f"{path}:{line} {name}"
        for path, line, name in _production_projection_calls()
        if path not in _ALLOWED_PROJECTION_FILES | _SANDBOX_REPLAY_FILES
    )
    assert not disallowed, (
        "production legacy projection returned; only deletion custody (`drop`) "
        f"and the pinned/explicit substrate may write the legacy index: {disallowed}"
    )


def test_the_projection_gate_is_gone() -> None:
    offenders = sorted(
        str(path.relative_to(ROOT))
        for root in ("nora", "scripts", "evals")
        for path in (ROOT / root).rglob("*.py")
        if "legacy_memory_projection" in path.read_text(encoding="utf-8")
    )
    assert not offenders, f"legacy_memory_projection opt-outs or gate remain: {offenders}"


def test_only_the_projector_writes_the_index() -> None:
    """The retained substrate still has its boundary: no second writer of memory tables."""
    index_tables = {name for name in schema.METADATA.tables
                    if name.startswith("memory_") and name != "legacy_memory_work_item"}
    assert index_tables == {
        "memory_node", "memory_edge", "memory_projection_queue", "memory_index_state",
    }
    writers = {
        (path, scope, table) for path, scope, table, _ in _writes()
        if table in index_tables
    }
    assert any(path == _PROJECTOR for path, _, _ in writers)  # positive control
    elsewhere = sorted(f"{p}::{s} ({t})" for p, s, t in writers if p != _PROJECTOR)
    assert not elsewhere, (
        "the memory index is written only by nora.infra.sql.memory_index, so a "
        f"node can never disagree with its source's projection: {elsewhere}"
    )


def test_each_write_form_is_recognised() -> None:
    """The write scan cannot pass on an empty result because a form was missed."""
    code = (
        "def f():\n"
        "    T = MESSAGES\n"
        "    sa.insert(MESSAGES); pg_insert(MEDIA_OBJECTS); sa.update(MESSAGE_EDIT)\n"
        "    MESSAGES.delete(); x.update(NOPE)\n"
        "    sa.update(schema.MESSAGES); sa.delete(T)\n"
        "    'UPDATE messages SET body = 1'; 'delete from \"media_objects\"'\n"
    )
    assert {table for _, table, _ in _scan(ast.parse(code))} == {
        "messages", "media_objects", "message_edit",
    }
    assert _scan(ast.parse("def f():\n    sa.update(schema.MESSAGES)\n")) == [
        ("f", "messages", False),
    ]
    assert _scan(ast.parse("def f():\n    T = MESSAGES\n    sa.delete(T)\n")) == [
        ("f", "messages", False),
    ]
    docstrings = '"""UPDATE messages SET x"""\ndef f():\n    "DELETE FROM messages"\n'
    assert _scan(ast.parse(docstrings)) == []
    assert _scan(ast.parse("def f():\n    x = 1\n    'DELETE FROM messages'\n")) == [
        ("f", "messages", False),
    ]


def test_only_a_memory_index_projection_call_counts_as_projecting() -> None:
    """A same-named method or the wrong `memory_index` module is not a projection."""
    def projects(code: str) -> bool:
        return all(p for _, _, p in _scan(ast.parse(code)))

    write = "    sa.update(MESSAGES)\n"
    assert not projects("def f(self):\n" + write + "    self._cache.project(1)\n")
    assert not projects("def f():\n" + write + "    project(conn, 'message', 1)\n")
    assert not projects(
        "from other import memory_index\n"
        "def f():\n" + write + "    memory_index.project(conn, 'message', 1)\n"
    )
    assert projects(
        "from nora.infra.sql import memory_index\n"
        "def f():\n" + write + "    memory_index.project(conn, 'message', 1)\n"
    )
    assert projects(
        "from nora.infra.sql import memory_index as mi\n"
        "def f():\n" + write + "    mi.reproject_media(conn, 'x')\n"
    )
    assert projects(
        "from nora.infra.sql.memory_index import project_episodes as pe\n"
        "def f():\n" + write + "    pe(conn, 1, 'stay', [])\n"
    )
