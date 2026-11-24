"""The Cognitive Graph's one Postgres store: create writes the registry row
and the kind row together, a revision appends against a current base and is
refused on a stale one, and a tombstone clears the row and its registry row
in the same transaction — so an injected failure mid-write leaves no
half-row (phase 1, item 6)."""

from __future__ import annotations

import asyncio
import itertools

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    ENTITY,
    INTENTION,
    REGISTRY,
)
from nora.memory.adapters.graph_store import SqlCognitiveGraph
from nora.memory.domain.graph import StaleBaseError

_ids = itertools.count()


def _row(kind: str, **over: object) -> dict:
    base = {
        "id": f"r-{next(_ids)}", "kind": kind, "unit": "u",
        "exposure": "internal", "writer": "test",
    }
    if kind == "entity":
        base.update(basis="computed", state="admitted", name="generated")
    elif kind == "claim":
        base.update(
            basis="inferred", reasoning="deduced", confidence="high",
            state="live", part="fact", content={"text": "generated"},
        )
    elif kind == "intention":
        base.update(
            basis="committed", state="open", owner="nora", owed_to="person:m",
            form="achieve", done_when_form="act_event",
            done_when={"act": "a"}, delivery_mode="when_it_comes_up",
        )
    else:
        base.update(
            basis="computed", state="live", type="about",
            src_id="ep-src", dst_id="ep-dst",
        )
    base.update(over)
    return base


@pytest.fixture
async def store(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        yield SqlCognitiveGraph(engine), engine
    finally:
        await engine.dispose()


async def test_create_writes_the_registry_row_and_the_kind_row(store) -> None:
    graph, engine = store
    await graph.create(CLAIM, _row("claim", id="c-1"))
    async with engine.connect() as conn:
        registry = (await conn.execute(
            sa.select(REGISTRY).where(REGISTRY.c.id == "c-1")
        )).mappings().one()
        claim = (await conn.execute(
            sa.select(CLAIM).where(CLAIM.c.id == "c-1")
        )).mappings().one()
    assert registry["kind"] == "claim" and registry["state"] == "live"
    assert claim["part"] == "fact"


async def test_revise_appends_against_the_current_base(store) -> None:
    graph, engine = store
    await graph.create(CLAIM, _row("claim", id="c-0"))
    await graph.revise(CLAIM, _row("claim", id="c-1", replaces="c-0"))
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(CLAIM).order_by(CLAIM.c.id)
        )).mappings().all()
    assert [r["id"] for r in rows] == ["c-0", "c-1"]
    assert rows[0]["state"] == "superseded"
    assert rows[1]["replaces"] == "c-0" and rows[1]["state"] == "live"


async def test_revise_against_a_stale_base_is_refused(store) -> None:
    graph, engine = store
    await graph.create(CLAIM, _row("claim", id="c-0"))
    await graph.revise(CLAIM, _row("claim", id="c-1", replaces="c-0"))
    with pytest.raises(StaleBaseError):
        await graph.revise(CLAIM, _row("claim", id="c-2", replaces="c-0"))
    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(REGISTRY)
            .where(REGISTRY.c.id == "c-2")
        )).scalar_one() == 0


@pytest.mark.parametrize("kind,table", [
    ("entity", ENTITY), ("claim", CLAIM), ("intention", INTENTION),
])
async def test_revise_writes_each_kinds_successor_state(store, kind, table) -> None:
    """Every kind revises: an entity's base keeps its lifecycle (it has no
    superseded state); a claim's or intention's base is superseded."""
    graph, engine = store
    await graph.create(table, _row(kind, id="b-0"))
    await graph.revise(table, _row(kind, id="b-1", replaces="b-0"))
    with pytest.raises(StaleBaseError):
        await graph.revise(table, _row(kind, id="b-2", replaces="b-0"))
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(table).order_by(table.c.id)
        )).mappings().all()
    assert [r["id"] for r in rows] == ["b-0", "b-1"]
    assert rows[1]["replaces"] == "b-0"
    expected = "admitted" if kind == "entity" else "superseded"
    assert rows[0]["state"] == expected


async def test_two_revisions_against_one_base_admit_exactly_one(store) -> None:
    """The first revision's transaction is held open; the second is observed
    blocked on it in Postgres before the first commits, so the two overlap."""
    graph, engine = store
    await graph.create(CLAIM, _row("claim", id="c-0"))
    first = _row("claim", id="c-1", replaces="c-0")
    async with engine.connect() as held:
        tx = await held.begin()
        await held.execute(sa.insert(REGISTRY).values(
            id=first["id"], kind=first["kind"], unit=first["unit"]))
        await held.execute(sa.insert(CLAIM).values(first))
        second = asyncio.create_task(
            graph.revise(CLAIM, _row("claim", id="c-2", replaces="c-0")))
        held_pid = (await held.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()
        pids: list[int] = []
        async with engine.connect() as probe:
            for _ in range(100):
                pids = (await probe.execute(sa.text(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE :held = ANY(pg_blocking_pids(pid))"
                ), {"held": held_pid})).scalars().all()
                if pids:
                    break
                await asyncio.sleep(0.05)
        assert len(pids) == 1, "the second revision was never blocked by the first"
        assert not second.done()
        await tx.commit()
    with pytest.raises(StaleBaseError):
        await second
    async with engine.connect() as conn:
        ids = (await conn.execute(
            sa.select(CLAIM.c.id).order_by(CLAIM.c.id)
        )).scalars().all()
        registry = (await conn.execute(
            sa.select(sa.func.count()).select_from(REGISTRY)
        )).scalar_one()
    assert len(ids) == 2 and "c-0" in ids
    assert registry == 2


async def test_tombstone_clears_the_row_and_its_registry_row(store) -> None:
    graph, engine = store
    await graph.create(ENTITY, _row("entity", id="e-1"))
    await graph.create(EDGE, _row("edge", id="g-1", src_id="e-1", dst_id="e-1"))
    await graph.tombstone(ENTITY, row_id="e-1", unit="u", content_hash="h1")
    async with engine.connect() as conn:
        registry = (await conn.execute(
            sa.select(REGISTRY).where(REGISTRY.c.id == "e-1")
        )).mappings().one()
        entity = (await conn.execute(
            sa.select(ENTITY).where(ENTITY.c.id == "e-1")
        )).mappings().one()
        edge = (await conn.execute(sa.text(
            "SELECT s.id FROM edge "
            "JOIN registry s ON s.id = edge.src_id AND s.unit = edge.unit "
            "WHERE edge.id = 'g-1'"
        ))).scalar_one()
    assert registry["state"] == "tombstoned"
    assert registry["content_hash"] == "h1" and registry["tombstoned_at"]
    assert entity["state"] == "tombstoned" and entity["name"] is None
    assert edge == "e-1"


async def test_an_injected_failure_after_the_registry_insert_leaves_neither_row(
    store,
) -> None:
    graph, engine = store
    broken = _row("claim", id="c-bad", part="opinion")
    with pytest.raises(sa.exc.IntegrityError):
        await graph.create(CLAIM, broken)
    async with engine.connect() as conn:
        for table in (REGISTRY, CLAIM):
            assert (await conn.execute(
                sa.select(sa.func.count()).select_from(table)
                .where(table.c.id == "c-bad")
            )).scalar_one() == 0


@pytest.mark.fresh_database
async def test_an_injected_failure_mid_tombstone_leaves_both_rows_unchanged(
    pg_url,
) -> None:
    engine = create_sql_engine(pg_url)
    try:
        graph = SqlCognitiveGraph(engine)
        await graph.create(ENTITY, _row("entity", id="e-1"))
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "CREATE OR REPLACE FUNCTION fail_row_update() RETURNS trigger "
                "LANGUAGE plpgsql AS $f$ BEGIN RAISE EXCEPTION 'injected'; END $f$"
            ))
            await conn.execute(sa.text(
                "CREATE TRIGGER fail_row_update BEFORE UPDATE ON entity "
                "FOR EACH ROW EXECUTE FUNCTION fail_row_update()"
            ))
        with pytest.raises(sa.exc.DBAPIError):
            await graph.tombstone(ENTITY, row_id="e-1", unit="u", content_hash="h1")
        async with engine.connect() as conn:
            registry = (await conn.execute(
                sa.select(REGISTRY).where(REGISTRY.c.id == "e-1")
            )).mappings().one()
            entity = (await conn.execute(
                sa.select(ENTITY).where(ENTITY.c.id == "e-1")
            )).mappings().one()
        assert registry["state"] == "live" and entity["state"] == "admitted"
        assert entity["name"] == "generated"
    finally:
        await engine.dispose()
