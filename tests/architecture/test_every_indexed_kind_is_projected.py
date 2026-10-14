"""Every write to an indexed table projects, and only the projector writes the
index (design/unified-memory/60-build-step-3.md S1, 25-memory-graph.md §7).

25 §7: «if a store's write path has no `project()` call, its rows are absent
from search». Nothing fails when that happens: the write works, the search
answers without the row, and nobody can tell a miss from an absence. So the
rule is held here, from source, the way `test_in_place_writes_keep_history.py`
holds history: every function under `nora/` (outside the migrations) that
writes a table in `nora.infra.sql.memory_index.INDEXED_WRITES` must itself
call `project`, `drop` or `reproject_media`, or be listed in `_NOT_PROJECTED`
with the reason it need not.

A write is `insert(T)` / `pg_insert(T)` / `update(T)` / `delete(T)` (bare or
`sa.`-qualified), `T.insert()` / `T.update()` / `T.delete()` on a table
constant, or raw SQL naming the table (`INSERT INTO t`, `UPDATE t SET`,
`DELETE FROM t`). A write whose table is a variable cannot be seen from
source: `nora.infra.sql.snapshot_at.seed` copies `messages` that way, into a
sandbox whose index is empty and reads `never` until a rebuild (60 §0.5).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from nora.infra.sql import schema
from nora.infra.sql.memory_index import INDEXED_WRITES

ROOT = Path(__file__).resolve().parents[2]
_PROJECTOR = "nora/infra/sql/memory_index.py"
_PROJECTING_CALLS = frozenset({"project", "drop", "reproject_media"})
_WRITE_FUNCS = frozenset({"insert", "pg_insert", "update", "delete"})
_RAW = re.compile(
    r'\bINSERT\s+INTO\s+"?([a-z_]+)'
    r'|\bUPDATE\s+"?([a-z_]+)"?\s+SET\b'
    r'|\bDELETE\s+FROM\s+"?([a-z_]+)',
    re.IGNORECASE,
)

#: `path::function` -> why that write to an indexed table projects nothing.
#: Empty since S1: every writer projects. A new entry needs a reason a reader
#: of the index would accept.
_NOT_PROJECTED: dict[str, str] = {}


_MEMORY_INDEX_MODULE = "nora.infra.sql.memory_index"


def _table_of(node: ast.expr, aliases: dict[str, str]) -> str | None:
    """A schema table named by `MESSAGES`, `schema.MESSAGES`, or a local
    alias of either (`T = MESSAGES; sa.update(T)`)."""
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


def _projector_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """What, in this module, IS the projector: names bound to the
    `memory_index` module, and names imported from it that are one of
    `project` / `drop` / `reproject_media` (under any alias). A method called
    `drop` on anything else is not a projection."""
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
                if alias.name == _MEMORY_INDEX_MODULE and alias.asname:
                    modules.add(alias.asname)
    return modules, functions


def _is_projector_call(node: ast.AST, modules: set[str], functions: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in functions
    if isinstance(func, ast.Attribute) and func.attr in _PROJECTING_CALLS:
        value = func.value
        if isinstance(value, ast.Name):
            return value.id in modules
        # `nora.infra.sql.memory_index.project(...)`, fully qualified.
        return ast.unparse(value) == _MEMORY_INDEX_MODULE
    return False


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _written_tables(node: ast.AST, aliases: dict[str, str] | None = None) -> set[str]:
    """The tables one AST node writes, by every form the module docstring names."""
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
    """Every write in one module: (enclosing function, table, projects?). The
    enclosing function is the innermost `def`; `<module>` if none."""
    modules, functions = _projector_names(tree)
    out: list[tuple[str, str, bool]] = []

    def visit(scope: ast.AST, name: str, inherited: dict[str, str]) -> None:
        aliases = _aliases(scope, inherited)
        projects = not isinstance(scope, ast.Module) and any(
            _is_projector_call(n, modules, functions) for n in _own_nodes(scope)
        )
        for node in _own_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node, node.name, aliases)
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


def test_the_scan_sees_the_known_projecting_writers() -> None:
    """Positive control: an empty scan must not read as «every write projects»."""
    sites = {(path, scope) for path, scope, table, _ in _writes() if table in INDEXED_WRITES}
    assert {
        ("nora/infra/sql/audit.py", "append"),
        ("nora/infra/sql/audit.py", "append_edit"),
        ("nora/infra/sql/audit.py", "hide_message"),
        ("nora/infra/sql/audit.py", "prune_all"),
        ("nora/infra/sql/media_objects.py", "comprehend"),
        ("nora/infra/sql/media_descriptions.py", "remember"),
    } <= sites


def test_every_write_to_an_indexed_table_projects_or_says_why_not() -> None:
    unprojected = sorted({
        f"{path}::{scope} ({table})" for path, scope, table, projects in _writes()
        if table in INDEXED_WRITES and not projects
        and f"{path}::{scope}" not in _NOT_PROJECTED
    })
    assert not unprojected, (
        "these functions write a table the memory index projects and call none "
        "of project / drop / reproject_media (nora.infra.sql.memory_index), so "
        "their rows would be silently absent from search. Project in the "
        f"write's own begin() block, or list it in _NOT_PROJECTED with a reason: {unprojected}"
    )


def test_every_not_projected_entry_is_still_a_write() -> None:
    sites = {
        f"{path}::{scope}" for path, scope, table, projects in _writes()
        if table in INDEXED_WRITES and not projects
    }
    stale = sorted(set(_NOT_PROJECTED) - sites)
    assert not stale, f"_NOT_PROJECTED names writes that no longer exist or now project: {stale}"


def test_only_the_projector_writes_the_index() -> None:
    index_tables = {name for name in schema.METADATA.tables if name.startswith("memory_")}
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
    """The forms, on code that uses them, so a scan edit that stops matching
    one cannot pass on an empty result."""
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
    # The two shapes that used to escape the scan, each alone.
    assert _scan(ast.parse("def f():\n    sa.update(schema.MESSAGES)\n")) == [
        ("f", "messages", False),
    ]
    assert _scan(ast.parse("def f():\n    T = MESSAGES\n    sa.delete(T)\n")) == [
        ("f", "messages", False),
    ]


def test_only_a_memory_index_call_counts_as_projecting() -> None:
    """A method that merely shares a name (`self._cache.drop`) used to pass
    for a projection. Only the `memory_index` module's own functions count,
    however they are imported."""
    def projects(code: str) -> bool:
        return all(p for _, _, p in _scan(ast.parse(code)))

    write = "    sa.update(MESSAGES)\n"
    assert not projects("def f(self):\n" + write + "    self._cache.drop(1)\n")
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
        "def f():\n" + write + "    mi.drop(conn, 'message', [1])\n"
    )
    assert projects(
        "from nora.infra.sql.memory_index import reproject_media as rm\n"
        "def f():\n" + write + "    rm(conn, 'x')\n"
    )
