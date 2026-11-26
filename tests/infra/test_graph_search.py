"""Exercise the write-time and rebuilt index with generated PostgreSQL rows."""

from dataclasses import replace

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import CLAIM, ENTITY, GRAPH_SEARCH
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.schema import MEMORY_NODE, MESSAGES, PRESENCE_FIXES
from nora.memory.adapters.graph_search import rebuild_graph_search
from nora.memory.domain.registration import AGGREGATE_FAMILIES, SEARCHABLE_SOURCES
from tests.infra.test_item_registration import _PERSON, _UNIT, _registration, _Writer
from tests.infra.test_item_registration import engine as graph_test_engine
from tests.integration.test_memory_vectors import MODEL, worker
from tests.integration.test_memory_vectors import llama as fake_llama

engine = graph_test_engine
llama = fake_llama


async def _message(engine: AsyncEngine, text: str = "Lanternmessage") -> str:
    registration = _registration()
    message = replace(_Writer(engine, registration)._message(), body=text)
    row_id = await SqlRecordStore(engine, registration=registration).append(
        message=message, person_id=_PERSON,
    )
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(MESSAGES.c.graph_id).where(
            MESSAGES.c.id == row_id,
        ))).scalar_one()


def _claim(row_id: str, text: str, **changes) -> dict:
    return {
        "id": row_id, "unit": _UNIT, "kind": "claim", "basis": "inferred",
        "reasoning": "deduced", "confidence": "medium", "state": "live",
        "exposure": "internal", "writer": "generated", "part": "fact",
        "content": {"ignored key": [20, False, {"nested": text}]},
        **changes,
    }


async def _indexed(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as conn:
        return dict((await conn.execute(sa.select(
            GRAPH_SEARCH.c.graph_id, GRAPH_SEARCH.c.text,
        ).where(GRAPH_SEARCH.c.unit == _UNIT))).all())


async def test_message_claim_and_entity_are_lexically_searchable_on_write(engine) -> None:
    message_id = await _message(engine)
    writer = SqlCognitiveGraph(engine)
    await writer.create(CLAIM, _claim("claim-lexical", "Lanternclaim"))
    await writer.create(ENTITY, {
        "id": "entity-lexical", "unit": _UNIT, "kind": "entity", "basis": "inferred",
        "reasoning": "deduced", "confidence": "high", "state": "admitted",
        "exposure": "internal", "writer": "generated",
        "name": "Lanternentity", "aliases": ["燈籠節"],
    })
    async with engine.connect() as conn:
        for word, expected in (
            ("Lanternmessage", message_id), ("Lanternclaim", "claim-lexical"),
            ("燈籠節", "entity-lexical"),
        ):
            top = await conn.scalar(sa.select(GRAPH_SEARCH.c.graph_id).where(
                GRAPH_SEARCH.c.unit == _UNIT,
            ).order_by(sa.func.similarity(GRAPH_SEARCH.c.text, word).desc()).limit(1))
            assert top == expected
    assert (await _indexed(engine))["claim-lexical"] == "Lanternclaim"


async def test_revision_adds_its_new_text_at_write_time(engine) -> None:
    writer = SqlCognitiveGraph(engine)
    await writer.create(CLAIM, _claim("initial-claim", "Lantern"))
    await writer.revise(CLAIM, _claim("revised-claim", "Harbour", replaces="initial-claim"))
    assert await _indexed(engine) == {"initial-claim": "Lantern", "revised-claim": "Harbour"}


async def test_tombstoned_item_leaves_the_index(engine) -> None:
    graph_id = await _message(engine)
    assert graph_id in await _indexed(engine)
    async with engine.begin() as conn:
        removed = await tombstone_items(conn, MESSAGES, MESSAGES.c.graph_id == graph_id)
        assert len(removed) == 1
        assert not await conn.scalar(sa.select(GRAPH_SEARCH.c.graph_id).where(
            GRAPH_SEARCH.c.graph_id == graph_id,
        ))


async def test_presence_fixes_and_aggregates_are_outside_the_closed_map(engine) -> None:
    writer = _Writer(engine, _registration())
    await writer.write("presence_fixes")
    graph_id = await _message(engine)
    async with engine.connect() as conn:
        fix_unit = await conn.scalar(sa.select(PRESENCE_FIXES.c.unit))
    assert fix_unit == _UNIT
    assert "presence_fixes" not in SEARCHABLE_SOURCES
    assert AGGREGATE_FAMILIES.keys().isdisjoint(SEARCHABLE_SOURCES)
    assert await _indexed(engine) == {graph_id: "Lanternmessage"}


async def test_rebuild_matches_incremental_after_revision_hide_and_tombstone(engine) -> None:
    removed = await _message(engine)
    writer = SqlCognitiveGraph(engine)
    await writer.create(CLAIM, _claim("base-claim", "Lantern"))
    await writer.revise(CLAIM, _claim("new-claim", "Harbour", replaces="base-claim"))
    await writer.create(CLAIM, _claim("hidden-claim", "Hidden", state="hidden"))
    async with engine.begin() as conn:
        await tombstone_items(conn, MESSAGES, MESSAGES.c.graph_id == removed)
    before = await _indexed(engine)
    assert before == {"base-claim": "Lantern", "new-claim": "Harbour"}
    async with engine.begin() as conn:
        await conn.execute(sa.delete(GRAPH_SEARCH).where(GRAPH_SEARCH.c.unit == _UNIT))
        await conn.execute(sa.insert(GRAPH_SEARCH).values(
            graph_id=removed, unit=_UNIT, text="Stale projection",
        ))
        await rebuild_graph_search(conn, _UNIT)
    assert await _indexed(engine) == before


async def test_worker_embeds_graph_search_without_embedding_the_legacy_index(engine, llama):
    _, url = llama
    graph_id = await _message(engine)
    async with engine.begin() as conn:
        row_id = await conn.scalar(sa.select(MESSAGES.c.id).where(
            MESSAGES.c.graph_id == graph_id,
        ))
        await memory_index.project(conn, "message", row_id)
    search_row = GRAPH_SEARCH.c.graph_id == graph_id
    async with engine.connect() as conn:
        graph_before = (await conn.execute(sa.select(GRAPH_SEARCH.c.embedding).where(
            search_row,
        ))).one()
        legacy_before = (await conn.execute(sa.select(MEMORY_NODE.c.embedding).where(
            MEMORY_NODE.c.kind == "message",
        ))).one()
    assert graph_before.embedding is None and legacy_before.embedding is None

    assert await worker(engine, url, legacy_index=False).run_once() == 1

    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(
            GRAPH_SEARCH.c.embedding, GRAPH_SEARCH.c.embedding_model, GRAPH_SEARCH.c.embed_input,
        ).where(search_row))).one()
        legacy = (await conn.execute(sa.select(MEMORY_NODE.c.embedding).where(
            MEMORY_NODE.c.kind == "message",
        ))).one()
    assert len(row.embedding) == 2048
    assert row.embedding_model == MODEL
    assert row.embed_input == "text"
    assert legacy.embedding is None
