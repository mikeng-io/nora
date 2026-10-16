"""The memory index's seeds against Postgres: the dense seed's two stages with
scope inside the HNSW scan, exact references pinned ahead of it, and the lexical
seed only as the fallback, answered `partial`.

Vectors are written straight into `memory_node` under the fingerprint the
search is configured with: what the property needs is a vector's distance to
the question, which the real model cannot be asked to produce on demand. The
question is embedded by `FakeQueryEmbedder`, the way `LlamaEmbedder.
embed_query` embeds it with the model.

The module's database plans with sequential and bitmap scans off: on a table
of a few hundred rows the planner would otherwise sort the whole table,
exactly, and no test here could tell an HNSW scan that loses its in-scope
candidates from one that keeps them. Production's tables are large enough to
choose the index on their own.

The policy: unit `u1` holds rooms B (Kit, Rin) and C (Rin); unit `u2` holds D.
Every read is from B.
"""

from __future__ import annotations

import math
import random
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nora.agent.memory_search import (
    PINNED_LIMIT,
    RESULT_LIMIT,
    MemorySearch,
    MemorySearchResult,
)
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.memory_index import ExactTerm, MemoryScope
from nora.domain.models import PersonRef
from nora.infra.sql import memory_read
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_index import EMBED_MAX_ATTEMPTS
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEMORY_EDGE, MEMORY_INDEX_STATE, MEMORY_NODE
from nora.obs.metrics import Metrics
from nora.testing.memory_index import DIMENSIONS, FakeQueryEmbedder

NOW = 1_790_000_000_000
MODEL = "model-a.gguf@0000+fmt1"
_B = RoomFacts("signal", frozenset({"b"}), "u1", frozenset({"kit", "rin"}), label="B")
_C = RoomFacts("signal", frozenset({"c"}), "u1", frozenset({"rin"}), label="C")
_D = RoomFacts("signal", frozenset({"d"}), "u2", frozenset({"kit"}), label="D")
_TARGET = RenderRoom(
    room=_B, rooms=(_B, _C, _D), circles=(frozenset({"kit", "rin"}),),
    live=LiveRoom(person_ids=frozenset({"kit", "rin"})),
)
_SCOPE = MemoryScope(
    units=("u1",), rooms=("signal:b", "signal:c"), listed_here=frozenset({"kit", "rin"}),
    people_here=frozenset({"kit", "rin"}), exclude_strict=False,
    presence_persons=frozenset(), room_persons={},
    social_off=frozenset(),
)
_RIN = PersonRef("rin", "Rin", "rin")
_KIT = PersonRef("kit", "Kit", "kit")


def _spread(rng: random.Random, lead: float) -> list[float]:
    """A unit vector `lead` along the question's axis plus noise on 63 others
    (all inside the indexed 256), so its cosine to the question is about
    `lead` over its norm."""
    vec = [0.0] * DIMENSIONS
    vec[0] = lead
    for d in range(1, 64):
        vec[d] = rng.gauss(0, 0.12)
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def axis(*weights: tuple[int, float]) -> list[float]:
    """A unit vector with the given weights on the given axes."""
    vec = [0.0] * DIMENSIONS
    for i, w in weights:
        vec[i] = w
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


#: The question's direction, and directions at a known cosine from it.
QUESTION = axis((0, 1.0))
SAME = QUESTION                                    # cosine 1.0
NEAR = axis((0, 0.9), (1, math.sqrt(1 - 0.81)))     # cosine 0.9
MID = axis((0, 0.5), (2, math.sqrt(1 - 0.25)))      # cosine 0.5
FAR = axis((3, 1.0))                               # cosine 0.0


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_dense_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
        conn.execute(f'ALTER DATABASE "{dbname}" SET enable_seqscan = off')
        conn.execute(f'ALTER DATABASE "{dbname}" SET enable_bitmapscan = off')
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


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    async with eng.begin() as conn:
        await conn.execute(sa.update(MEMORY_INDEX_STATE).values(status="complete"))
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("TRUNCATE memory_node, memory_edge"))
        await eng.dispose()


async def _node(
    engine: Any, sid: str, text: str, vector: list[float] | None, *, model: str = MODEL,
    kind: str = "message", at: int = NOW - 3_600_000, attempts: int = 0, **scope: Any,
) -> None:
    if not scope:
        scope = {"origin_room": "signal:b"}
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_NODE).values(
            kind=kind, source_id=sid, text=text, at_ms=at, embedding=vector,
            embedding_model=model if vector is not None else None,
            embed_attempts=attempts, **scope,
        ))


async def _search(
    engine: Any, query: str = "the question", *, embedder: FakeQueryEmbedder | None = None,
    people: tuple[PersonRef, ...] = (), metrics: Metrics | None = None,
) -> MemorySearchResult:
    embedder = embedder or FakeQueryEmbedder({query: QUESTION})
    return await MemorySearch(
        SqlMemoryIndex(engine), metrics or Metrics(), embedder=embedder, model=MODEL,
    ).search(query=query, person_id=None, target=_TARGET, depth="low", now_ms=NOW,
             people=people, social_off=())


def _ids(found: MemorySearchResult) -> list[str]:
    return [h.node.source_id for h in found.hits]


# --- the dense seed -------------------------------------------------------------


async def test_dense_seeds_rank_by_cosine_and_embed_the_question_once(engine: Any) -> None:
    await _node(engine, "near", "first", NEAR)
    await _node(engine, "mid", "second", MID)
    await _node(engine, "far", "third", FAR)
    embedder = FakeQueryEmbedder({"the question": QUESTION})
    found = await _search(engine, embedder=embedder)
    assert _ids(found) == ["near", "mid"]  # cosine 0 is under the relevance floor
    assert [round(h.score, 2) for h in found.hits] == [0.9, 0.5]
    assert embedder.calls == ["the question"]
    assert found.status == "ok"


async def test_another_units_node_is_never_returned_however_close(engine: Any) -> None:
    await _node(engine, "other-unit", "first", SAME, unit="u2")
    await _node(engine, "other-room", "first", SAME, origin_room="signal:d")
    await _node(engine, "mine", "second", MID)
    found = await _search(engine)
    assert _ids(found) == ["mine"]


async def test_a_vector_of_another_model_is_never_compared(engine: Any) -> None:
    await _node(engine, "old-model", "first", SAME, model="model-b.gguf@1111+fmt1")
    await _node(engine, "current", "second", MID)
    found = await _search(engine)
    assert _ids(found) == ["current"]
    # The other model's node waits for a vector of this one: not complete.
    assert found.status == "partial"


async def test_the_scan_keeps_going_past_out_of_scope_neighbours(engine: Any) -> None:
    """More out-of-scope nodes than the scan's candidate list, every one
    nearer than the one in scope: without `hnsw.iterative_scan` the HNSW scan
    returns its `ef_search` nearest, the scope filter drops them all, and the
    room gets nothing. The crowd is spread around the question (a tight
    cluster of near-copies prunes the graph's links to the one node outside
    it, which no setting can then reach)."""
    rng = random.Random(7)
    crowd = [_spread(rng, 1.0) for _ in range(
        memory_read.HNSW_EF_SEARCH + memory_read.DENSE_CANDIDATES)]
    mine = _spread(rng, 0.6)
    # Positive control on the geometry: every one of the crowd is nearer.
    assert min(v[0] for v in crowd) > mine[0]
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_NODE), [
            {"kind": "message", "source_id": f"d{i}", "text": f"crowd {i}",
             "at_ms": NOW - 3_600_000, "origin_room": "signal:d",
             "embedding": vec, "embedding_model": MODEL}
            for i, vec in enumerate(crowd)
        ])
    await _node(engine, "mine", "second", mine)
    found = await _search(engine)
    assert _ids(found) == ["mine"]


async def test_the_planner_scans_the_hnsw_index_with_the_scope_inside(engine: Any) -> None:
    await _node(engine, "mine", "second", MID)
    stmt = memory_read._dense_statement(QUESTION, _SCOPE, model=MODEL, limit=20).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    )
    async with engine.begin() as conn:
        plan = "\n".join((await conn.exec_driver_sql(f"EXPLAIN {stmt}")).scalars())
    assert "Index Scan using idx_memory_node_embedding_256" in plan, plan
    # The scope and the fingerprint are the index scan's own filter, not a
    # step after it: the line under the scan names both.
    scan = plan.split("idx_memory_node_embedding_256", 1)[1]
    filter_line = next(line for line in scan.splitlines() if "Filter:" in line)
    assert "embedding_model" in filter_line and "unit" in filter_line, plan


# --- exact seeds -------------------------------------------------------------------


async def test_an_exact_name_outranks_a_closer_dense_neighbour(engine: Any) -> None:
    """A literal name match must not lose to a near-duplicate. The near-duplicate
    is the question's own vector and newer."""
    await _node(engine, "named", "Rin booked the table", MID, at=NOW - 86_400_000 * 30)
    await _node(engine, "near-duplicate", "Ren booked the table", SAME, at=NOW - 3_600_000)
    found = await _search(engine, "did Rin book the table",
                          embedder=FakeQueryEmbedder({"did Rin book the table": QUESTION}),
                          people=(_RIN, _KIT))
    assert _ids(found) == ["named", "near-duplicate"]
    assert found.hits[0].path[0].how == "exact" and found.hits[0].pin > 0
    assert found.hits[1].pin == 0


async def test_literal_hits_hold_half_the_answer_and_a_cosine_one_hit_still_shows(
    engine: Any,
) -> None:
    """With every exact hit pinned, ten nodes naming Rin would fill every slot and
    the question's own near-duplicate would never show. The first `PINNED_LIMIT` are
    pinned; the rest rank by their cosine."""
    for i in range(12):
        await _node(engine, f"named{i:02}", f"Rin note {i}", MID, at=NOW - 86_400_000 * (i + 1))
    await _node(engine, "twin", "a twin with no name", SAME, at=NOW - 86_400_000 * 40)
    q = "what did Rin say"
    found = await _search(engine, q, embedder=FakeQueryEmbedder({q: QUESTION}),
                          people=(_RIN,))
    pinned = [h for h in found.hits if h.pin]
    assert len(pinned) == PINNED_LIMIT and list(found.hits[:PINNED_LIMIT]) == pinned
    assert found.hits[PINNED_LIMIT].node.source_id == "twin"
    assert len(found.hits) == RESULT_LIMIT


async def test_an_exact_seed_is_whole_and_in_scope(engine: Any) -> None:
    await _node(engine, "kitchen", "the kitchen is closed", FAR)
    await _node(engine, "elsewhere", "Kit said hi", FAR, unit="u2")
    await _node(engine, "named", "Kit's bike is red", FAR)
    found = await _search(engine, "what about Kit",
                          embedder=FakeQueryEmbedder({"what about Kit": QUESTION}),
                          people=(_KIT,))
    assert _ids(found) == ["named"]


async def test_handles_urls_and_ids_are_exact_and_the_nearest_match_goes_first(
    engine: Any,
) -> None:
    await _node(engine, "h1", "follow @night_owl for the photos", FAR)
    await _node(engine, "h2", "@night_owl posted again", NEAR)
    await _node(engine, "url", "menu at https://example.org/menu", FAR)
    await _node(engine, "id", "booking ref ZX4471 confirmed", FAR)
    await _node(engine, "not-id", "booking confirmed", FAR)
    q = "what did @night_owl post, and https://example.org/menu, ref ZX4471"
    found = await _search(engine, q, embedder=FakeQueryEmbedder({q: QUESTION}))
    assert set(_ids(found)) == {"h1", "h2", "url", "id"}
    assert _ids(found).index("h2") < _ids(found).index("h1")  # nearer, same terms held


# --- the fallback, and coverage ----------------------------------------------------


async def test_embedder_down_answers_partial_with_lexical_results(engine: Any) -> None:
    await _node(engine, "lex", "ramen crawl tonight", SAME)
    metrics = Metrics()
    found = await _search(engine, "ramen crawl", embedder=FakeQueryEmbedder(fail=OSError()),
                          metrics=metrics)
    assert _ids(found) == ["lex"]
    assert found.hits[0].path[0].how == "matched"
    assert found.status == "partial"
    empty = await _search(engine, "nothing like this",
                          embedder=FakeQueryEmbedder(fail=OSError()), metrics=metrics)
    assert (empty.status, empty.hits) == ("partial", ())
    assert metrics.get("nora_memory_search_total", status="partial") == 2


async def test_no_vectors_yet_falls_back_without_asking_the_embedder(engine: Any) -> None:
    await _node(engine, "lex", "ramen crawl tonight", None)
    embedder = FakeQueryEmbedder()
    found = await _search(engine, "ramen crawl", embedder=embedder)
    assert (_ids(found), found.status, embedder.calls) == (["lex"], "partial", [])


async def test_unembedded_nodes_keep_it_partial_and_given_up_ones_are_read_by_their_words(
    engine: Any,
) -> None:
    """A node waiting for a vector holds the answer `partial`. One the worker
    gave up on does not (it waits for nothing), and since no dense seed can
    reach it, the lexical seed reads it: never a confident `nothing_found`
    for words that are there."""
    await _node(engine, "mine", "second", MID)
    await _node(engine, "waiting", "the ramen crawl plan", None)
    assert (await _search(engine)).status == "partial"
    async with engine.begin() as conn:
        await conn.execute(sa.update(MEMORY_NODE).where(MEMORY_NODE.c.source_id == "waiting")
                           .values(embed_attempts=EMBED_MAX_ATTEMPTS))
    found = await _search(engine)
    assert (found.status, _ids(found)) == ("ok", ["mine"])
    q = "the ramen crawl"
    found = await _search(engine, q, embedder=FakeQueryEmbedder({q: FAR}))
    assert (found.status, _ids(found)) == ("ok", ["waiting"])
    assert found.hits[0].path[0].how == "matched"
    assert (await _search(engine, "nothing near",
                          embedder=FakeQueryEmbedder({"nothing near": FAR}))).status \
        == "nothing_found"


async def test_a_node_waiting_in_another_unit_does_not_make_this_room_partial(
    engine: Any,
) -> None:
    """Coverage is counted within the search's scope."""
    await _node(engine, "mine", "second", MID)
    await _node(engine, "waiting-elsewhere", "third", None, unit="u2")
    assert (await _search(engine)).status == "ok"
    await _node(engine, "waiting-here", "fourth", None)
    assert (await _search(engine)).status == "partial"


async def test_an_attachment_with_nothing_to_embed_does_not_hold_it_partial(
    engine: Any,
) -> None:
    """A text-less attachment that is not an image fails as `empty` and
    waits for its reading, not for the worker."""
    await _node(engine, "mine", "second", MID)
    await _node(engine, "1:abc", "", None, kind="media", attempts=1)
    async with engine.begin() as conn:
        await conn.execute(sa.update(MEMORY_NODE).where(MEMORY_NODE.c.kind == "media")
                           .values(embed_error="empty"))
    assert (await _search(engine)).status == "ok"


async def test_a_short_dense_scan_is_counted(engine: Any, monkeypatch: Any) -> None:
    """When `hnsw.max_scan_tuples` stops the scan before it finds the scope's
    vectors, the search says so on a counter (no content)."""
    rng = random.Random(11)
    crowd = [_spread(rng, 1.0) for _ in range(300)]
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_NODE), [
            {"kind": "message", "source_id": f"d{i}", "text": f"crowd {i}",
             "at_ms": NOW - 3_600_000, "origin_room": "signal:d",
             "embedding": vec, "embedding_model": MODEL}
            for i, vec in enumerate(crowd)
        ])
    for i in range(3):
        await _node(engine, f"mine{i}", "second", _spread(rng, 0.3))
    metrics = Metrics()
    found = await _search(engine, metrics=metrics)
    assert len(found.hits) == 3  # positive control: the whole scope is found
    assert metrics.get("nora_memory_dense_short_total") == 0
    # pgvector does not bound the initial scan (`ef_search`), only the
    # iterative one after it: both small, so the scan stops in the crowd.
    monkeypatch.setattr(memory_read, "HNSW_EF_SEARCH", 10)
    monkeypatch.setattr(memory_read, "HNSW_MAX_SCAN_TUPLES", 20)
    found = await _search(engine, metrics=metrics)
    assert len(found.hits) < 3
    assert metrics.get("nora_memory_dense_short_total") == 1


async def test_the_exact_read_is_served_by_the_trigram_index_for_every_term_kind(
    engine: Any,
) -> None:
    """A CJK term's `strpos` in the same OR as the regular expressions would make
    the whole exact read a sequential scan. Each kind has a form
    `idx_memory_node_text_trgm` serves. The plan is asked of the terms' OR alone:
    with the scope beside it, a table this small is read through the scope's own
    indexes, which says nothing about the terms."""
    await _node(engine, "mine", "Kit 阿豹妹 ZX4471", MID)
    terms = [ExactTerm("Kit", cased=True), ExactTerm("阿豹妹", cased=True),
             ExactTerm("阿豹妹"), ExactTerm("ZX4471")]
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL enable_bitmapscan = on"))
        n = MEMORY_NODE.alias("n")
        stmt = sa.select(n.c.id).where(
            sa.or_(*[memory_read._holds(n.c.text, t) for t in terms]),
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        plan = "\n".join((await conn.exec_driver_sql(f"EXPLAIN {stmt}")).scalars())
    assert "idx_memory_node_text_trgm" in plan and "Seq Scan" not in plan, plan
    assert len(await SqlMemoryIndex(engine).exact(terms, _SCOPE, limit=5)) == 1


async def test_a_hub_never_seeds_densely(engine: Any) -> None:
    await _node(engine, "kit", "Kit", SAME, kind="person", origin_room=None)
    await _node(engine, "e1", "dinner", MID, kind="event", unit="u1")
    async with engine.begin() as conn:
        await conn.execute(pg_insert(MEMORY_EDGE).values(
            src_kind="event", src_id="e1", rel="about", dst_kind="person", dst_id="kit",
            provenance="test",
        ))
    found = await _search(engine, "Kit", embedder=FakeQueryEmbedder({"Kit": QUESTION}),
                          people=(_KIT,))
    assert _ids(found) == ["e1"] and all(not h.node.is_hub for h in found.hits)
