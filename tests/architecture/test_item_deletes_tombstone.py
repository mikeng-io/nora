"""Item source deletes must preserve their registry endpoints."""

import ast

from nora.memory.domain.registration import ITEM_SOURCES
from tests.architecture.test_every_indexed_kind_is_projected import (
    ROOT,
    _aliases,
    _callee,
    _own_nodes,
    _table_of,
)

_WRITER = "nora/infra/sql/item_retention.py"
_SEED = (
    "nora/subject/lexical_seed.py",
    "sa.delete(MESSAGES).where(_OURS).returning(MESSAGES.c.id)",
)


def _violations(path: str, source: str) -> list[int]:
    tree = ast.parse(source, filename=path)
    lines: list[int] = []
    names = {source.upper(): source for source in ITEM_SOURCES}
    delete_calls = {"delete"}
    for imported in ast.walk(tree):
        if not isinstance(imported, ast.ImportFrom):
            continue
        for alias in imported.names:
            if alias.asname and alias.name in names:
                names[alias.asname] = names[alias.name]
            if alias.name == "delete" and imported.module == "sqlalchemy":
                delete_calls.add(alias.asname or alias.name)

    def visit(scope: ast.AST, inherited: dict[str, str]) -> None:
        aliases = _aliases(scope, inherited)
        for node in _own_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node, aliases)
            if not isinstance(node, ast.Call) or _callee(node) not in delete_calls:
                continue
            table = node.args[0] if node.args else (
                node.func.value if isinstance(node.func, ast.Attribute) else node.func
            )
            name = _table_of(table, aliases)
            if isinstance(table, (ast.Name, ast.Attribute)):
                attribute = table.id if isinstance(table, ast.Name) else table.attr
                name = name or names.get(attribute)
            if (
                path == _WRITER and isinstance(scope, ast.AsyncFunctionDef)
                and scope.name == "tombstone_items"
            ):
                continue
            if (
                name is None and isinstance(table, ast.Subscript)
                and isinstance(table.slice, ast.Constant)
            ):
                name = table.slice.value
            if name not in ITEM_SOURCES:
                continue
            if path == _SEED[0] and _SEED[1] == source.splitlines()[node.lineno - 1].strip():
                continue
            lines.append(node.lineno)

    visit(tree, {})
    return lines


def test_all_item_deletes_go_through_the_tombstone_writer() -> None:
    violations = []
    for path in (ROOT / "nora").rglob("*.py"):
        if "migrations" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(
            f"{relative}:{line}" for line in _violations(relative, path.read_text())
        )
    assert not violations, violations


def test_item_delete_check_catches_a_new_module() -> None:
    source = (
        "from nora.infra.sql.schema import MESSAGES\n"
        "import sqlalchemy as sa\n"
        "sa.delete(MESSAGES)"
    )
    assert _violations("nora/generated_new_writer.py", source) == [3]
