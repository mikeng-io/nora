"""Shared contract for the memory index's read (`nora.domain.memory_index.
MemoryIndexRead`, design/unified-memory/25-memory-graph.md §4). Every
implementation runs every test.

Two implementations: `nora.infra.sql.memory_read.SqlMemoryIndex` over real
Postgres, and `nora.testing.memory_index.FakeMemoryIndex`, which the walk's
unit tests use. The property is the scope: each read returns only in-scope
nodes, and a hub only as a direct neighbour. One database for the module,
truncated between tests. Texts are whole words, where pg_trgm and the fake's
containment agree.

The seeds of 65 V2 too: `dense` compares only vectors of the asked model,
nearest first; `exact` matches a term literally and whole (an ASCII term not
inside a longer word, any other script as a substring); `index_state` with a
model counts the vector coverage, leaving out nodes the worker gave up on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import replace
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nora.domain.memory_index import (
    ExactTerm,
    IndexedNode,
    IndexState,
    MemoryFilter,
    MemoryIndexRead,
    MemoryScope,
    in_scope,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_index import EMBED_MAX_ATTEMPTS
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEMORY_EDGE, MEMORY_INDEX_STATE, MEMORY_NODE
from nora.testing.memory_index import DIMENSIONS, FakeMemoryIndex

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


Vectors = dict[tuple[str, str], tuple[str, list[float]]]
Load = Callable[..., Awaitable[MemoryIndexRead]]


@pytest.fixture(params=["fake", "sql"])
async def load(request: Any) -> AsyncIterator[Load]:
    """`load(nodes, edges, vectors={key: (model, vector)}, given_up={key})`."""
    if request.param == "fake":
        async def fake(
            nodes: list[IndexedNode], edges: list[Any], vectors: Vectors | None = None,
            given_up: frozenset[tuple[str, str]] = frozenset(),
        ) -> MemoryIndexRead:
            return FakeMemoryIndex(nodes, edges, vectors=vectors, given_up=given_up)
        yield fake
        return
    engine = create_sql_engine(request.getfixturevalue("_db"))

    async def sql(
        nodes: list[IndexedNode], edges: list[Any], vectors: Vectors | None = None,
        given_up: frozenset[tuple[str, str]] = frozenset(),
    ) -> MemoryIndexRead:
        async with engine.begin() as conn:
            await conn.execute(sa.update(MEMORY_INDEX_STATE).values(status="complete"))
            for n in nodes:
                model, vector = (vectors or {}).get(n.key, (None, None))
                await conn.execute(pg_insert(MEMORY_NODE).values(
                    kind=n.kind, source_id=n.source_id, text=n.text, at_ms=n.at_ms,
                    unit=n.unit, origin_room=n.origin_room, persons=list(n.persons),
                    exclude_persons=list(n.exclude_persons),
                    lineage=n.lineage if n.lineage is not None else [], state=n.state,
                    embedding=vector, embedding_model=model,
                    embed_attempts=EMBED_MAX_ATTEMPTS if n.key in given_up else 0,
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


# --- the seeds of 65 V2 ---------------------------------------------------------

M = "model-a"


def _unit(*weights: tuple[int, float]) -> list[float]:
    vec = [0.0] * DIMENSIONS
    for i, w in weights:
        vec[i] = w
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


Q = _unit((0, 1.0))


async def test_dense_compares_only_the_models_in_scope_vectors_nearest_first(load: Load) -> None:
    nodes = [
        IndexedNode("message", "near", "a", 3, origin_room="signal:b"),
        IndexedNode("note", "mid", "b", 2, unit="u1"),
        IndexedNode("note", "other-unit", "c", 1, unit="u2"),
        IndexedNode("message", "other-model", "d", 1, origin_room="signal:b"),
        IndexedNode("message", "none", "e", 1, origin_room="signal:b"),
        IndexedNode("person", "kit", "Kit"),
    ]
    vectors = {
        ("message", "near"): (M, _unit((0, 0.9), (1, 0.43589))),
        ("note", "mid"): (M, _unit((0, 0.5), (2, 0.86603))),
        ("note", "other-unit"): (M, Q),
        ("message", "other-model"): ("model-b", Q),
        ("person", "kit"): (M, Q),
    }
    index = await load(nodes, [], vectors)
    seeds = await index.dense(Q, SCOPE, model=M, limit=10)
    assert [s.node.source_id for s in seeds] == ["near", "mid"]
    assert [round(s.score, 2) for s in seeds] == [0.9, 0.5]
    assert [s.node.source_id for s in await index.dense(Q, SCOPE, model=M, limit=1)] == ["near"]


async def test_exact_matches_whole_terms_in_scope_most_terms_then_nearest_first(
    load: Load,
) -> None:
    nodes = [
        IndexedNode("message", "kitchen", "the kitchen sink", 9, origin_room="signal:b"),
        IndexedNode("message", "both", "Kit and @night_owl", 1, origin_room="signal:b"),
        IndexedNode("message", "far", "Kit's bike", 8, origin_room="signal:b"),
        IndexedNode("message", "near", "with Kit today", 2, origin_room="signal:b"),
        IndexedNode("message", "upper", "KIT is not the name as written", 9,
                    origin_room="signal:b"),
        IndexedNode("note", "cjk", "阿豹妹今日去咗行山", 3, unit="u1"),
        IndexedNode("message", "owlish", "@night_owlet", 4, origin_room="signal:b"),
        IndexedNode("note", "other-unit", "Kit too", 9, unit="u2"),
    ]
    vectors = {("message", "near"): (M, Q), ("message", "far"): (M, _unit((3, 1.0)))}
    index = await load(nodes, [], vectors)
    seeds = await index.exact(
        [ExactTerm("Kit", cased=True), ExactTerm("@Night_Owl")], SCOPE, limit=10,
        vector=Q, model=M,
    )
    # A name only as written (not `KIT`); a handle in any case.
    assert [s.node.source_id for s in seeds] == ["both", "near", "far"]
    assert all(s.score == 1.0 for s in seeds)
    assert [None if s.near is None else round(s.near, 2) for s in seeds] == [None, 1.0, 0.0]
    # Any other script is a substring: Cantonese writes no spaces.
    assert [s.node.source_id
            for s in await index.exact([ExactTerm("阿豹")], SCOPE, limit=10)] == ["cjk"]
    assert await index.exact([], SCOPE, limit=10) == []


async def test_coverage_counts_embedded_and_waiting_and_not_the_given_up(load: Load) -> None:
    nodes = [
        IndexedNode("message", "done", "a", 1, origin_room="signal:b"),
        IndexedNode("message", "waiting", "b", 1, origin_room="signal:b"),
        IndexedNode("message", "stale", "c", 1, origin_room="signal:b"),
        IndexedNode("message", "given-up", "d", 1, origin_room="signal:b"),
        IndexedNode("stay", "s9", "stay", 1, persons=("kit",)),  # lexical only
        IndexedNode("person", "kit", "Kit"),                     # a hub
    ]
    vectors = {("message", "done"): (M, Q), ("message", "stale"): ("model-b", Q)}
    index = await load(nodes, [], vectors, frozenset({("message", "given-up")}))
    assert await index.index_state(model=M) == IndexState("complete", 0, 1, 2, 1)
    assert await index.index_state() == IndexState("complete", 0)


async def test_coverage_counts_within_a_scope_and_lexical_reads_the_given_up(load: Load) -> None:
    """Review of #31: a node waiting in another unit does not hold this
    room's answers `partial`; a node the worker gave up on, which no dense
    seed reaches, is still found by its words."""
    nodes = [
        IndexedNode("message", "done", "ramen crawl", 1, origin_room="signal:b"),
        IndexedNode("message", "given-up", "ramen night", 2, origin_room="signal:b"),
        IndexedNode("message", "waiting-elsewhere", "ramen too", 3, unit="u2"),
        IndexedNode("message", "given-up-elsewhere", "ramen also", 4, unit="u2"),
    ]
    index = await load(nodes, [], {("message", "done"): (M, Q)},
                       frozenset({("message", "given-up"), ("message", "given-up-elsewhere")}))
    assert await index.index_state(model=M, scope=SCOPE) == IndexState("complete", 0, 1, 0, 1)
    assert await index.index_state(model=M) == IndexState("complete", 0, 1, 1, 2)
    found = await index.seed(["ramen"], SCOPE, limit=10, given_up_of=M)
    assert [s.node.source_id for s in found] == ["given-up"]


# ---------------------------------------------------------------------------
# the question's narrowing (`MemoryScope.only`, `memory_search`'s filters)
# ---------------------------------------------------------------------------


def _only(**kw: Any) -> MemoryScope:
    return replace(SCOPE, only=MemoryFilter(**kw))


#: `NODES`' in-scope three: message 1 (at 10, room b, authored by kit), note
#: n1 (at 9, the unit, about kit), stay s1 (at 3, kit's).
@pytest.mark.parametrize(("narrowing", "expected"), [
    ({"kinds": ("note",)}, {("note", "n1")}),
    ({"kinds": ("message", "stay")}, {("message", "1"), ("stay", "s1")}),
    ({"since_ms": 9}, {("message", "1"), ("note", "n1")}),
    ({"until_ms": 5}, {("stay", "s1")}),
    ({"since_ms": 9, "until_ms": 9}, {("note", "n1")}),
    ({"person": "kit"}, {("message", "1"), ("note", "n1"), ("stay", "s1")}),
    ({"person": "sam"}, set()),
    ({"person": "rin"}, set()),  # not listed here: nothing about them reads
])
async def test_a_narrowing_applies_inside_the_seed(
    load: Load, narrowing: dict[str, Any], expected: set[tuple[str, str]],
) -> None:
    index = await _graph(load)
    seeds = await index.seed(["lantern", "festival"], _only(**narrowing), limit=50)
    assert {s.node.key for s in seeds} == expected


async def test_a_person_narrowing_keeps_the_listed_room_rule(load: Load) -> None:
    """Kit's own message in room c, which does not list kit, is in scope and
    still not evidence about kit (35 §3, the `about` seed's rule)."""
    nodes = [
        IndexedNode("message", "b1", "lantern", 2, origin_room="signal:b"),
        IndexedNode("message", "c1", "lantern", 1, origin_room="signal:c"),
    ]
    edges = [
        ("message", "b1", "authored_by", "person", "kit"),
        ("message", "c1", "authored_by", "person", "kit"),
    ]
    index = await load(nodes, edges)
    assert {s.node.key for s in await index.seed(["lantern"], SCOPE, limit=50)} == {
        ("message", "b1"), ("message", "c1")}
    seeds = await index.seed(["lantern"], _only(person="kit"), limit=50)
    assert {s.node.key for s in seeds} == {("message", "b1")}


async def test_a_narrowing_applies_inside_dense_exact_about_and_index_state(
    load: Load,
) -> None:
    nodes = [
        IndexedNode("message", "near", "lantern", 3, origin_room="signal:b"),
        IndexedNode("note", "mid", "lantern", 2, unit="u1"),
    ]
    vectors = {
        ("message", "near"): (M, _unit((0, 0.9), (1, 0.43589))),
        ("note", "mid"): (M, _unit((0, 0.5), (2, 0.86603))),
    }
    index = await load(nodes, [("note", "mid", "about", "person", "kit")], vectors)
    notes = _only(kinds=("note",))
    assert [s.node.source_id for s in await index.dense(Q, notes, model=M, limit=10)] == [
        "mid"]
    assert [s.node.source_id for s in await index.exact(
        [ExactTerm("lantern")], notes, limit=10)] == ["mid"]
    assert await index.about("kit", _only(kinds=("message",)), limit=10) == []
    state = await index.index_state(model=M, scope=_only(until_ms=2))
    assert (state.embedded, state.unembedded) == (1, 0)


async def test_a_narrowing_drops_non_hub_neighbours_and_keeps_hubs(load: Load) -> None:
    index = await _graph(load)
    found = await index.neighbours([("message", "1")], _only(kinds=("message",)))
    assert {nb.node.key for nb in found} == {("person", "kit")}
