"""Shared contract for the memory index's read (`nora.domain.memory_index.
MemoryIndexRead`, design/unified-memory/25-memory-graph.md §4). Every
implementation runs every test.

Two implementations: `nora.infra.sql.memory_read.SqlMemoryIndex` over real
Postgres, and `nora.testing.memory_index.FakeMemoryIndex`, which the walk's
unit tests use. The property is the scope: each read returns only in-scope
nodes, and a hub only as a direct neighbour. One database for the module,
truncated between tests. Texts are whole words, where pg_trgm and the fake's
containment agree.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nora.domain.memory_index import (
    IndexedNode,
    IndexState,
    MemoryIndexRead,
    MemoryScope,
    in_scope,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEMORY_EDGE, MEMORY_INDEX_STATE, MEMORY_NODE
from nora.testing.memory_index import FakeMemoryIndex

SCOPE = MemoryScope(
    units=("u1",), rooms=("signal:b", "signal:c"),
    listed_here=frozenset({"kit", "sam"}), people_here=frozenset({"kit", "sam"}),
    exclude_strict=False, presence_persons=frozenset({"kit"}),
    room_persons={"signal:b": frozenset({"kit", "sam"}), "signal:c": frozenset({"sam"})},
)

#: (node, in scope for SCOPE)
NODES: list[tuple[IndexedNode, bool]] = [
    (IndexedNode("message", "1", "lantern festival", 10, origin_room="signal:b"), True),
    (IndexedNode("note", "n1", "lantern festival plan", 9, unit="u1"), True),
    (IndexedNode("note", "n2", "lantern festival elsewhere", 8, unit="u2"), False),
    (IndexedNode("message", "2", "lantern festival", 7, origin_room="signal:d"), False),
    (IndexedNode("character", "c1", "lantern festival fan", 6, unit="u1",
                 persons=("rin",)), False),
    (IndexedNode("note", "n3", "lantern festival surprise", 5, unit="u1",
                 exclude_persons=("sam",)), False),
    (IndexedNode("note", "n4", "lantern festival old", 4, unit="u1", state="retired"), False),
    (IndexedNode("stay", "s1", "lantern festival stay", 3, persons=("kit",),
                 lineage=[["presence", "kit", None]]), True),
    (IndexedNode("stay", "s2", "lantern festival stay", 3, persons=("sam",),
                 lineage=[["presence", "sam", None]]), False),
    (IndexedNode("place", "p1", "lantern festival square"), False),
    (IndexedNode("person", "kit"), False),
]
EDGES = [
    ("note", "n1", "cites", "message", "1"),
    ("note", "n2", "cites", "message", "1"),
    ("message", "1", "authored_by", "person", "kit"),
    ("note", "n1", "about", "place", "p1"),
    ("note", "n1", "about", "person", "kit"),
    ("note", "n2", "about", "person", "kit"),
    ("stay", "s1", "about", "person", "kit"),
    ("note", "n1", "cites", "message", "999"),  # dangling: no node
]


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_read_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


Load = Callable[[list[IndexedNode], list[tuple[str, str, str, str, str]]],
                Awaitable[MemoryIndexRead]]


@pytest.fixture(params=["fake", "sql"])
async def load(request: Any) -> AsyncIterator[Load]:
    if request.param == "fake":
        async def fake(nodes: list[IndexedNode], edges: list[Any]) -> MemoryIndexRead:
            return FakeMemoryIndex(nodes, edges)
        yield fake
        return
    engine = create_sql_engine(request.getfixturevalue("_db"))

    async def sql(nodes: list[IndexedNode], edges: list[Any]) -> MemoryIndexRead:
        async with engine.begin() as conn:
            await conn.execute(sa.update(MEMORY_INDEX_STATE).values(status="complete"))
            for n in nodes:
                await conn.execute(pg_insert(MEMORY_NODE).values(
                    kind=n.kind, source_id=n.source_id, text=n.text, at_ms=n.at_ms,
                    unit=n.unit, origin_room=n.origin_room, persons=list(n.persons),
                    exclude_persons=list(n.exclude_persons),
                    lineage=n.lineage if n.lineage is not None else [], state=n.state,
                ))
            for sk, si, rel, dk, di in edges:
                await conn.execute(pg_insert(MEMORY_EDGE).values(
                    src_kind=sk, src_id=si, rel=rel, dst_kind=dk, dst_id=di,
                    provenance="test",
                ))
        return SqlMemoryIndex(engine)

    try:
        yield sql
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("TRUNCATE memory_node, memory_edge"))
        await engine.dispose()


async def _graph(load: Load) -> MemoryIndexRead:
    return await load([n for n, _ in NODES], EDGES)


def test_the_python_predicate_states_the_expected_scope() -> None:
    assert {n.key for n, ok in NODES if ok} == {n.key for n, _ in NODES if in_scope(n, SCOPE)}


async def test_seed_returns_only_in_scope_non_hub_nodes(load: Load) -> None:
    index = await _graph(load)
    seeds = await index.seed(["lantern", "festival"], SCOPE, limit=50)
    assert {s.node.key for s in seeds} == {n.key for n, ok in NODES if ok}
    assert all(s.score > 0 for s in seeds)


async def test_seed_with_no_terms_reads_nothing(load: Load) -> None:
    index = await _graph(load)
    assert await index.seed([], SCOPE, limit=50) == []


async def test_a_strict_room_drops_every_node_that_excludes_anyone(load: Load) -> None:
    index = await _graph(load)
    strict = MemoryScope(
        units=SCOPE.units, rooms=SCOPE.rooms, listed_here=frozenset({"kit"}),
        people_here=frozenset({"kit"}), exclude_strict=True,
        presence_persons=frozenset({"kit"}),
    )
    loose = MemoryScope(
        units=SCOPE.units, rooms=SCOPE.rooms, listed_here=frozenset({"kit"}),
        people_here=frozenset({"kit"}), exclude_strict=False,
        presence_persons=frozenset({"kit"}),
    )
    assert ("note", "n3") not in {s.node.key for s in await index.seed(
        ["surprise"], strict, limit=50)}
    assert ("note", "n3") in {s.node.key for s in await index.seed(
        ["surprise"], loose, limit=50)}


async def test_about_returns_in_scope_nodes_about_the_person(load: Load) -> None:
    index = await _graph(load)
    found = await index.about("kit", SCOPE, limit=50)
    assert {n.key for n in found} == {("note", "n1"), ("stay", "s1")}


async def test_neighbours_both_directions_hubs_kept_out_of_scope_and_dangling_dropped(
    load: Load,
) -> None:
    index = await _graph(load)
    found = await index.neighbours([("message", "1"), ("note", "n1")], SCOPE)
    got = {(nb.via, nb.rel, nb.outgoing, nb.node.key) for nb in found}
    assert got == {
        (("message", "1"), "cites", False, ("note", "n1")),
        (("message", "1"), "authored_by", True, ("person", "kit")),
        (("note", "n1"), "cites", True, ("message", "1")),
        (("note", "n1"), "about", True, ("place", "p1")),
        (("note", "n1"), "about", True, ("person", "kit")),
    }


async def test_state_reads_the_index_state(load: Load) -> None:
    index = await _graph(load)
    assert await index.index_state() == IndexState("complete", 0)
