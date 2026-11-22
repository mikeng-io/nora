"""Subject and tally graph reads apply the shared visibility predicate."""

import ast

from nora.infra.sql import cognitive_graph_schema
from nora.infra.sql.cognitive_graph_store import GRAPH_READERS
from nora.memory.domain.registration import AGGREGATE_FAMILIES, ITEM_SOURCES, NON_GRAPH_TABLES
from tests.architecture.test_every_indexed_kind_is_projected import ROOT, _callee

_GRAPH_TABLES = ITEM_SOURCES.keys() | AGGREGATE_FAMILIES.keys() | {
    name for name, category in NON_GRAPH_TABLES.items() if category == "graph kind"
}
_GRAPH_NAMES = {
    name for name, value in vars(cognitive_graph_schema).items()
    if getattr(value, "name", None) in _GRAPH_TABLES
} | {name.upper() for name in _GRAPH_TABLES}


def _unfiltered(source: str) -> list[int]:
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    bindings = set(_GRAPH_NAMES)
    select_calls = {"select"}
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            bindings.update(alias.asname for alias in node.names
                            if alias.name in _GRAPH_NAMES and alias.asname)
            if node.module == "sqlalchemy":
                select_calls.update(alias.asname or alias.name for alias in node.names
                                    if alias.name == "select")

    def names_graph(expr: ast.AST) -> bool:
        for part in ast.walk(expr):
            match part:
                case ast.Name(id=name) | ast.Attribute(attr=name) if name in bindings:
                    return True
                case ast.Subscript(value=ast.Attribute(attr="tables"), slice=key):
                    if not isinstance(key, ast.Constant) or key.value in _GRAPH_TABLES:
                        return True
        return False

    previous = -1
    while previous != len(bindings):
        previous = len(bindings)
        for node in nodes:
            if isinstance(node, ast.Assign) and names_graph(node.value):
                bindings.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )

    def selection(call: ast.Call) -> tuple[list[ast.AST], list[ast.AST]]:
        sources = list(call.args)
        predicates: list[ast.AST] = []
        while isinstance(member := parents.get(call), ast.Attribute) and member.value is call:
            method = parents.get(member)
            if not isinstance(method, ast.Call) or method.func is not member:
                break
            if member.attr in {"select_from", "join", "join_from", "outerjoin", "outerjoin_from"}:
                sources.extend(method.args)
            if member.attr in {"where", "filter"}:
                predicates.extend(method.args)
            if member.attr in {"where", "filter", "order_by", "group_by", "having"}:
                sources.extend(method.args)
            call = method
        return sources, predicates

    violations = []
    for node in nodes:
        if isinstance(node, ast.Call) and _callee(node) in select_calls:
            sources, predicates = selection(node)
            if any(names_graph(expr) for expr in sources) and not any(
                isinstance(call, ast.Call) and _callee(call) == "visible_to_reads"
                for expr in predicates for call in ast.walk(expr)
            ):
                violations.append(node.lineno)
    return sorted(violations)


def test_graph_reader_selects_apply_visibility() -> None:
    assert GRAPH_READERS
    unfiltered = {}
    for module in GRAPH_READERS:
        path = ROOT / (module.replace(".", "/") + ".py")
        if lines := _unfiltered(path.read_text()):
            unfiltered[module] = lines
    assert not unfiltered, unfiltered


def test_graph_reads_check_catches_a_new_select_in_a_listed_module() -> None:
    source = """
from nora.infra.sql.cognitive_graph_schema import CLAIM as T
import sqlalchemy as sa
from sqlalchemy import select as sql_select
sa.select(T.c.id)
sa.select(T.c.id).where(visible_to_reads(T))
sa.select(sa.func.count()).select_from(T)
sql_select(sa.func.count()).where(T.c.state == "live")
"""
    assert _unfiltered(source) == [5, 7, 8]
