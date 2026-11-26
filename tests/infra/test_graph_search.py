"""Exercise the write-time and rebuilt index with generated PostgreSQL rows."""

import logging
from dataclasses import replace

import pytest
import sqlalchemy as sa
from aiohttp.test_utils import TestServer
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.embedder import LlamaEmbedder, fingerprint, model_file
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import CLAIM, ENTITY, GRAPH_SEARCH
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.embed_schedule import EMBED_MAX_ATTEMPTS
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.memory_embed import MemoryEmbedWorker
from nora.infra.sql.schema import MEMORY_NODE, MESSAGES, PRESENCE_FIXES
from nora.memory.adapters.graph_search import rebuild_graph_search
from nora.memory.domain.registration import AGGREGATE_FAMILIES, SEARCHABLE_SOURCES
from nora.obs.metrics import Metrics
from tests.infra.test_item_registration import _PERSON, _UNIT, _registration, _Writer
from tests.infra.test_item_registration import engine as graph_test_engine
from tests.unit.test_embedder import FakeLlamaServer

MODEL_ID = "model-a.gguf@0000"
MODEL = fingerprint(MODEL_ID)

engine = graph_test_engine


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def llama():
    fake = FakeLlamaServer()
    server = TestServer(fake.app())
    await server.start_server()
    try:
        yield fake, str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


def worker(engine, url, *, metrics=None, clock=None):
    return MemoryEmbedWorker(
        engine, LlamaEmbedder(url), model=MODEL, model_file=model_file(MODEL_ID), batch=16,
        metrics=metrics, clock=clock or Clock(),
    )


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

    assert await worker(engine, url).run_once() == 1

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

async def test_an_unavailable_embedder_never_gives_a_node_up(engine, llama):
    """An outage backs a graph row off, never past the cap; it embeds when the server returns."""
    fake, url = llama
    graph_id = await _message(engine)
    clock = Clock()
    w = MemoryEmbedWorker(
        engine, LlamaEmbedder(url), model=MODEL, model_file=model_file(MODEL_ID),
        batch=16, clock=clock,
    )
    assert await w.run_once() == 1
    async with engine.begin() as conn:
        await conn.execute(sa.update(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.graph_id == graph_id,
        ).values(embedding=None, embedding_model=None, embed_attempts=0))
    fake.status, fake.healthy = 503, False
    for _ in range(EMBED_MAX_ATTEMPTS + 2):
        clock.now += 7 * 3600
        assert await w.run_once() == 1, "still picked: never given up"
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(
            GRAPH_SEARCH.c.embedding, GRAPH_SEARCH.c.embed_attempts,
        ).where(GRAPH_SEARCH.c.graph_id == graph_id))).one()
    assert row.embedding is None
    assert row.embed_attempts < EMBED_MAX_ATTEMPTS
    fake.status, fake.healthy = 200, True
    clock.now += 7 * 3600
    assert await w.run_once() == 1
    row = await _graph_row(engine, graph_id)
    assert row["embedding"] is not None
    assert (row["embed_attempts"], row["embed_error"]) == (0, None)



async def _graph_row(engine, graph_id):
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.graph_id == graph_id,
        ))).mappings().one()


async def test_the_worker_refuses_a_wrong_model_then_embeds_the_configured_one(engine, llama):
    fake, url = llama
    graph_id = await _message(engine)
    fake.model_path = "/models/some-other-model.gguf"
    w = worker(engine, url)
    assert await w.run_once() == 0
    assert fake.calls == 0

    fake.model_path = "/models/model-a.gguf"
    assert await w.run_once() == 1
    row = await _graph_row(engine, graph_id)
    assert row["embedding"] is not None and row["embedding_model"] == MODEL


async def test_a_restart_clears_only_stale_model_vectors_and_reembeds_them(engine, llama):
    fake, url = llama
    stale_id = await _message(engine, "embedded by the old model")
    current_id = await _message(engine, "embedded by the current model")
    stale_vector = [-0.5] + [0.0] * 2047
    current_vector = [0.5] + [0.0] * 2047
    old_model = fingerprint("model-b.gguf@1111")
    async with engine.begin() as conn:
        await conn.execute(sa.update(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.graph_id == stale_id,
        ).values(embedding=stale_vector, embedding_model=old_model,
                 embedded_at=1, embed_input="text"))
        await conn.execute(sa.update(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.graph_id == current_id,
        ).values(embedding=current_vector, embedding_model=MODEL,
                 embedded_at=1, embed_input="text"))

    assert await worker(engine, url).run_once() == 1

    stale = await _graph_row(engine, stale_id)
    current = await _graph_row(engine, current_id)
    assert fake.calls == 1
    assert stale["embedding_model"] == MODEL
    assert list(stale["embedding"]) != stale_vector
    assert (stale["embed_attempts"], stale["embed_error"]) == (0, None)
    assert list(current["embedding"]) == current_vector
    assert current["embedding_model"] == MODEL


async def test_an_outage_stops_the_batch_backs_off_immediately_and_a_rejection_continues(
    engine, llama, caplog,
):
    fake, url = llama
    first = await _message(engine, "a private sentence one")
    second = await _message(engine, "a private sentence two")
    metrics, clock = Metrics(), Clock()
    w = worker(engine, url, metrics=metrics, clock=clock)

    fake.status, fake.healthy = 503, False
    with caplog.at_level(logging.DEBUG):
        assert await w.run_once() == 1
    assert fake.calls == 1
    rows = [await _graph_row(engine, row_id) for row_id in (first, second)]
    assert sorted(row["embed_attempts"] for row in rows) == [0, 1]
    failed = max(rows, key=lambda row: row["embed_attempts"])
    assert failed["embed_error"].startswith("unavailable:")
    assert "private" not in caplog.text and "private" not in failed["embed_error"]
    assert metrics.get("nora_memory_embed_total", outcome="unavailable") == 1

    assert await w.run_once() == 1
    assert fake.calls == 2
    assert await w.run_once() == 0
    assert fake.calls == 2

    fake.status, fake.healthy = 400, True
    clock.now += 7 * 3600
    assert await w.run_once() == 2
    assert metrics.get("nora_memory_embed_total", outcome="rejected") == 2


async def test_an_unexpected_failure_is_recorded_and_given_up_after_the_cap(
    engine, llama, monkeypatch,
):
    _fake, url = llama
    bad = await _message(engine, "a graph row that always breaks")
    good = await _message(engine, "a graph row that embeds")
    real = LlamaEmbedder.embed_document

    async def _breaks(self, text: str):
        if "breaks" in text:
            raise KeyError("boom")
        return await real(self, text)

    monkeypatch.setattr(LlamaEmbedder, "embed_document", _breaks)
    metrics, clock = Metrics(), Clock()
    w = worker(engine, url, metrics=metrics, clock=clock)

    assert await w.run_once() == 2
    bad_row = await _graph_row(engine, bad)
    assert (bad_row["embed_attempts"], bad_row["embed_error"]) == (1, "error:KeyError")
    assert (await _graph_row(engine, good))["embedding"] is not None
    assert metrics.get("nora_memory_embed_total", outcome="error") == 1

    for _ in range(EMBED_MAX_ATTEMPTS - 1):
        clock.now += 7 * 3600
        assert await w.run_once() == 1
    assert metrics.get("nora_memory_embed_total", outcome="given_up") == 1
    clock.now += 7 * 3600
    assert await w.run_once() == 0


async def test_truncation_is_counted_only_when_the_vector_is_stored(
    engine, llama, monkeypatch,
):
    _fake, url = llama
    long_id = await _message(engine, "x" * 9000)
    short_id = await _message(engine, "a short message")
    metrics = Metrics()
    assert await worker(engine, url, metrics=metrics).run_once() == 2
    assert (await _graph_row(engine, long_id))["embedding"] is not None
    assert (await _graph_row(engine, short_id))["embedding"] is not None
    assert metrics.get("nora_memory_embed_total", outcome="ok") == 2
    assert metrics.get("nora_memory_embed_truncated_total", kind="graph_search") == 1

    changed_id = await _message(engine, "y" * 9000)
    real = LlamaEmbedder.embed_document

    async def _changed_meanwhile(self, text: str):
        out = await real(self, text)
        async with engine.begin() as conn:
            await conn.execute(sa.update(GRAPH_SEARCH).where(
                GRAPH_SEARCH.c.graph_id == changed_id,
            ).values(text="short now"))
        return out

    monkeypatch.setattr(LlamaEmbedder, "embed_document", _changed_meanwhile)
    metrics = Metrics()
    assert await worker(engine, url, metrics=metrics).run_once() == 1
    assert (await _graph_row(engine, changed_id))["embedding"] is None
    assert metrics.get("nora_memory_embed_truncated_total", kind="graph_search") == 0
