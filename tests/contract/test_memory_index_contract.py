"""Shared contract for the memory index's read (`nora.domain.memory_index.
MemoryIndexRead`). Every implementation runs every test.

Two implementations: `nora.infra.sql.memory_read.SqlMemoryIndex` over real
Postgres, and `nora.testing.memory_index.FakeMemoryIndex`, which the walk's
unit tests use. The property is the scope: each read returns only in-scope
nodes, and a hub only as a direct neighbour. One database for the module,
truncated between tests. Texts are whole words, where pg_trgm and the fake's
containment agree.

The seeds: `dense` compares only vectors of the asked model,
nearest first; `exact` matches a term literally and whole (an ASCII term not
inside a longer word, any other script as a substring); `index_state` with a
model counts the vector coverage, leaving out nodes the worker gave up on,
and, given a window, how many waiting nodes were projected within it.
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
    social_off=frozenset(),
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
    """`load(nodes, edges, vectors={key: (model, vector)}, given_up={key},
    fresh={key})`: `fresh` nodes have owed their vector since now, every
    other since a day ago."""
    if request.param == "fake":
        async def fake(
            nodes: list[IndexedNode], edges: list[Any], vectors: Vectors | None = None,
            given_up: frozenset[tuple[str, str]] = frozenset(),
            fresh: frozenset[tuple[str, str]] = frozenset(),
        ) -> MemoryIndexRead:
            return FakeMemoryIndex(nodes, edges, vectors=vectors, given_up=given_up,
                                   fresh=fresh)
        yield fake
        return
    engine = create_sql_engine(request.getfixturevalue("_db"))

    async def sql(
        nodes: list[IndexedNode], edges: list[Any], vectors: Vectors | None = None,
        given_up: frozenset[tuple[str, str]] = frozenset(),
        fresh: frozenset[tuple[str, str]] = frozenset(),
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
                    sha256=n.sha256,
                    owed_since=(
                        sa.func.clock_timestamp() if n.key in fresh
                        else sa.func.clock_timestamp() - sa.text("interval '1 day'")
                    ),
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
        presence_persons=frozenset({"kit"}), social_off=frozenset(),
    )
    loose = MemoryScope(
        units=SCOPE.units, rooms=SCOPE.rooms, listed_here=frozenset({"kit"}),
        people_here=frozenset({"kit"}), exclude_strict=False,
        presence_persons=frozenset({"kit"}), social_off=frozenset(),
    )
    assert ("note", "n3") not in {s.node.key for s in await index.seed(
        ["surprise"], strict, limit=50)}
    assert ("note", "n3") in {s.node.key for s in await index.seed(
        ["surprise"], loose, limit=50)}


async def test_about_returns_in_scope_nodes_about_the_person(load: Load) -> None:
    index = await _graph(load)
    found = await index.about("kit", SCOPE, limit=50)
    assert {n.key for n in found} == {("note", "n1"), ("stay", "s1")}


async def test_media_returns_in_scope_nodes_holding_the_bytes_newest_first(
    load: Load,
) -> None:
    sha, other = "ab" * 32, "cd" * 32
    index = await load([
        IndexedNode("media", "a1", "poster", 5, origin_room="signal:b", sha256=sha),
        IndexedNode("media", "a2", "poster again", 9, origin_room="signal:c", sha256=sha),
        IndexedNode("media", "a3", "poster", 7, origin_room="signal:d", sha256=sha),
        IndexedNode("media", "a4", "a menu", 8, origin_room="signal:b", sha256=other),
    ], [])
    found = await index.media([sha], SCOPE, limit=50)
    assert [n.key for n in found] == [("media", "a2"), ("media", "a1")]
    assert await index.media([], SCOPE, limit=50) == []
    assert [n.key for n in await index.media([sha], SCOPE, limit=1)] == [("media", "a2")]


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


async def test_places_are_the_place_hubs_the_question_names_and_a_place_hub_descends(
    load: Load,
) -> None:
    """Step 3a: `places` returns the place hubs whose «中文 / English» name
    (either part) the question holds as an exact reference does, whatever
    their scope (a hub has none), and nothing else: not a hub of another
    kind, not a nameless place, not a name inside a longer word, not a name
    too short to name anything (one Chinese character, two Latin letters).
    From a
    place on the frontier, `neighbours` reaches what lies in it (a contained
    place, an in-scope outing) and not an out-of-scope one."""
    mk = IndexedNode("place", "district:旺角 / Mong Kok", "旺角 / Mong Kok")
    venue = IndexedNode("place", "v1", "Langham Place")
    kit = [["presence", "kit", None]]
    index = await load(
        [mk, venue, IndexedNode("place", "v2", ""), IndexedNode("person", "kit"),
         IndexedNode("place", "district:Mong", "Mong"),
         IndexedNode("place", "district:MK", "MK"), IndexedNode("place", "city:中", "中"),
         IndexedNode("outing", "o1", "outing", 1, unit="u1", persons=("kit",), lineage=kit),
         IndexedNode("outing", "o2", "outing", 1, unit="u2", persons=("kit",), lineage=kit)],
        [("place", "v1", "located_in", "place", mk.source_id),
         ("outing", "o1", "located_in", "place", mk.source_id),
         ("outing", "o2", "located_in", "place", mk.source_id)],
    )
    assert [n.key for n in await index.places("dinner near mong kok?", limit=5)] == [
        ("place", "district:Mong"), mk.key,
    ]
    assert [n.key for n in await index.places("旺角邊間好", limit=5)] == [mk.key]
    assert [n.key for n in await index.places("Mongkok Langham Place", limit=5)] == [
        venue.key,
    ]
    assert await index.places("", limit=5) == []
    assert await index.places("lunch in MK, 中環", limit=5) == []
    found = await index.neighbours([mk.key], SCOPE)
    assert {(nb.rel, nb.outgoing, nb.node.key) for nb in found} == {
        ("located_in", False, venue.key), ("located_in", False, ("outing", "o1")),
    }


async def test_state_reads_the_index_state(load: Load) -> None:
    index = await _graph(load)
    assert await index.index_state() == IndexState("complete", 0)


# --- the seeds ------------------------------------------------------------------

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
    """A node waiting in another unit does not hold this
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
    found = await index.seed(["ramen"], SCOPE, limit=10, unembedded_of=M)
    assert [s.node.source_id for s in found] == ["given-up"]


async def test_a_fresh_wait_is_counted_apart_and_lexical_reads_every_unembedded_node(
    load: Load,
) -> None:
    """A node projected within the window is `fresh`; one waiting longer,
    or a given-up one, is not. Out of scope, neither is counted. The
    lexical seed over unembedded nodes reads waiting and given-up ones alike,
    in scope only, and never a node holding a current vector."""
    nodes = [
        IndexedNode("message", "done", "ramen crawl", 1, origin_room="signal:b"),
        IndexedNode("message", "just-said", "ramen tonight", 5, origin_room="signal:b"),
        IndexedNode("message", "old-wait", "ramen again", 4, origin_room="signal:b"),
        IndexedNode("message", "given-up", "ramen night", 3, origin_room="signal:b"),
        IndexedNode("message", "fresh-elsewhere", "ramen too", 6, unit="u2"),
        # Embeddable from its bytes, but no seed reads it: never fresh.
        IndexedNode("media", "1:abc", "", 7, origin_room="signal:b", sha256="ab" * 32),
    ]
    index = await load(
        nodes, [], {("message", "done"): (M, Q)}, frozenset({("message", "given-up")}),
        frozenset({("message", "just-said"), ("message", "fresh-elsewhere"),
                   ("message", "done"), ("message", "given-up"), ("media", "1:abc")}),
    )
    assert await index.index_state(model=M, scope=SCOPE, fresh_seconds=180) \
        == IndexState("complete", 0, 1, 3, 1, 1)
    # No window: nothing is fresh, every wait is a backlog.
    assert await index.index_state(model=M, scope=SCOPE) == IndexState("complete", 0, 1, 3, 1)
    found = await index.seed(["ramen"], SCOPE, limit=10, unembedded_of=M)
    assert [s.node.source_id for s in found] == ["just-said", "old-wait", "given-up"]


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
    still not evidence about kit (the `about` seed's rule)."""
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


# --- the social stream's nodes (design/cognition-loop/22 §9) ------------------

#: (node, in scope for SCOPE, in scope once kit has set `social = false`).
#: A social node is its author's: readable only where the room lists them.
#: `social = false` removes every one of theirs, recorded before the switch or
#: not, and nothing else of theirs: a Character account about kit stays.
SOCIAL_NODES: list[tuple[IndexedNode, bool, bool]] = [
    (IndexedNode("social", "instagram:1", "Instagram story\npicnic plans", 10,
                 persons=("kit",)), True, False),
    (IndexedNode("social_media", "instagram:1:" + "a1" * 32, "picnic plans blanket", 10,
                 persons=("kit",), sha256="a1" * 32), True, False),
    (IndexedNode("social_location", "instagram:1", "Picnic Lawn", 10,
                 persons=("kit",)), True, False),
    (IndexedNode("social", "instagram:2", "Instagram post\npicnic plans", 9,
                 persons=("sam",)), True, True),
    (IndexedNode("social", "instagram:3", "Instagram reel\npicnic plans", 8,
                 persons=("rin",)), False, False),
    (IndexedNode("character", "c9", "picnic plans fan", 7, unit="u1",
                 persons=("kit",)), True, True),
]
OFF = replace(SCOPE, social_off=frozenset({"kit"}))


def test_the_python_predicate_states_the_social_scope_and_its_opt_out() -> None:
    assert [in_scope(n, SCOPE) for n, _, _ in SOCIAL_NODES] == [
        ok for _, ok, _ in SOCIAL_NODES
    ]
    assert [in_scope(n, OFF) for n, _, _ in SOCIAL_NODES] == [
        ok for _, _, ok in SOCIAL_NODES
    ]


async def test_every_read_leaves_out_an_opted_out_persons_social_nodes(load: Load) -> None:
    """The SQL filter and the Python one agree, inside each read: seed, exact,
    about, neighbours and dense."""
    vectors = {n.key: (M, _unit((0, 1.0))) for n, _, _ in SOCIAL_NODES}
    index = await load(
        [n for n, _, _ in SOCIAL_NODES] + [IndexedNode("person", "kit")],
        [("social", "instagram:1", "authored_by", "person", "kit"),
         ("social_media", "instagram:1:" + "a1" * 32, "attached_to", "social", "instagram:1"),
         ("social_location", "instagram:1", "attached_to", "social", "instagram:1"),
         ("character", "c9", "about", "person", "kit")],
        vectors=vectors,
    )
    for scope, column in ((SCOPE, 1), (OFF, 2)):
        want = {n.key for n, *ok in SOCIAL_NODES if ok[column - 1]}
        assert {s.node.key for s in await index.seed(["picnic"], scope, limit=50)} == want
        assert {s.node.key for s in await index.exact(
            [ExactTerm("picnic")], scope, limit=50)} == want
        assert {s.node.key for s in await index.dense(
            _unit((0, 1.0)), scope, model=M, limit=50)} == want
    assert {n.key for n in await index.about("kit", OFF, limit=10)} == {("character", "c9")}
    found = await index.neighbours([("social", "instagram:1")], SCOPE)
    assert {("social_media", "instagram:1:" + "a1" * 32), ("social_location", "instagram:1")} <= {
        nb.node.key for nb in found
    }
    gone = await index.neighbours([("social", "instagram:1")], OFF)
    assert {nb.node.key for nb in gone} == {("person", "kit")}, "a hub stays a neighbour"


#: (node, in scope for SCOPE). A social item's tagged place is a node of its
#: own, gated as a stay is: in scope only where its author passes presence
#: (kit does here, sam does not). The items themselves are not gated by it.
LOCATED: list[tuple[IndexedNode, bool]] = [
    (IndexedNode("social", "instagram:1", "Instagram post\npicnic plans", 10,
                 persons=("kit",)), True),
    (IndexedNode("social_location", "instagram:1", "Lantern Cove", 10,
                 persons=("kit",)), True),
    (IndexedNode("social", "instagram:2", "Instagram post\npicnic plans", 9,
                 persons=("sam",)), True),
    (IndexedNode("social_location", "instagram:2", "Lantern Cove", 9,
                 persons=("sam",)), False),
]
NO_PRESENCE = replace(SCOPE, presence_persons=frozenset())


def test_the_python_predicate_gates_a_tagged_place_by_presence() -> None:
    assert [in_scope(n, SCOPE) for n, _ in LOCATED] == [ok for _, ok in LOCATED]
    assert [n.key for n, _ in LOCATED if in_scope(n, NO_PRESENCE)] == [
        ("social", "instagram:1"), ("social", "instagram:2"),
    ]


async def test_every_read_gates_a_tagged_place_by_presence_and_keeps_its_item(
    load: Load,
) -> None:
    """The SQL filter and the Python one agree, inside each read: a tagged
    place is seeded, matched exactly, found dense and reached as a neighbour
    only where its author passes presence; its item is found by its caption
    either way."""
    vectors = {n.key: (M, _unit((0, 1.0))) for n, _ in LOCATED}
    index = await load(
        [n for n, _ in LOCATED],
        [("social_location", f"instagram:{i}", "attached_to", "social", f"instagram:{i}")
         for i in (1, 2)],
        vectors=vectors,
    )
    kit_place = {("social_location", "instagram:1")}
    for scope, want in ((SCOPE, kit_place), (NO_PRESENCE, set())):
        assert {s.node.key for s in await index.seed(["Lantern"], scope, limit=50)} == want
        assert {s.node.key for s in await index.exact(
            [ExactTerm("Lantern Cove")], scope, limit=50)} == want
        assert {s.node.key for s in await index.dense(
            _unit((0, 1.0)), scope, model=M, limit=50) if s.node.kind == "social_location"
        } == want
        assert {s.node.key for s in await index.seed(["picnic"], scope, limit=50)} == {
            ("social", "instagram:1"), ("social", "instagram:2"),
        }, "the item stays findable by its caption"
    reached = await index.neighbours([("social", "instagram:1"), ("social", "instagram:2")], SCOPE)
    assert {nb.node.key for nb in reached} == kit_place
    assert await index.neighbours([("social", "instagram:1")], NO_PRESENCE) == []


async def test_profile_render_switch_hides_persisted_nodes_on_every_read(load: Load) -> None:
    profile = IndexedNode("profile_line", "p1", "lantern picnic", 10, unit="u1",
                          persons=("kit",))
    note = IndexedNode("note", "control", "lantern picnic", 9, unit="u1", persons=("kit",))
    index = await load([profile, note, IndexedNode("person", "kit")], [
        ("profile_line", "p1", "about", "person", "kit"),
        ("note", "control", "about", "person", "kit"),
        ("note", "control", "cites", "profile_line", "p1"),
    ], vectors={n.key: (M, _unit((0, 1.0))) for n in (profile, note)})
    for render in (True, False, True):
        scope = replace(SCOPE, profile_render=render)
        want = {note.key, profile.key} if render else {note.key}
        assert {s.node.key for s in await index.seed(["lantern"], scope, limit=10)} == want
        assert {s.node.key for s in await index.exact(
            [ExactTerm("lantern")], scope, limit=10)} == want
        assert {s.node.key for s in await index.dense(
            _unit((0, 1.0)), scope, model=M, limit=10)} == want
        assert {n.key for n in await index.about("kit", scope, limit=10)} == want
        found = await index.neighbours([note.key], scope)
        assert (profile.key in {n.node.key for n in found}) == render
        assert in_scope(profile, scope) == render
