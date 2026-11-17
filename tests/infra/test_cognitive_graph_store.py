"""The Cognitive Graph's one Postgres store: create writes the registry row
and the kind row together, a revision appends against a current base and is
refused on a stale one, and a tombstone clears the row and its registry row
in the same transaction — so an injected failure mid-write leaves no
half-row (phase 1, item 6)."""

from __future__ import annotations

import itertools

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    ENTITY,
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
