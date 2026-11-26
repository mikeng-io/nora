"""The retained legacy memory index's vector invalidation and `owed_since` clock."""
from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.infra.embedder import fingerprint
from nora.infra.sql import memory_index
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS, MEMORY_NODE
from tests.fixtures.legacy_projection import ProjectingRecordStore

pytestmark = pytest.mark.asyncio

MODEL = fingerprint("model-a.gguf@0000")


def _message(body: str, *, ts: int = 1000) -> NormalizedMessage:
    return NormalizedMessage(
        "signal", "g1", "group", "+10000000001", "Member A", ts, body, {},
    )


@pytest.fixture
async def engine(clean_pg_url: str):
    eng = create_sql_engine(clean_pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _vector() -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = 1.0
    return vector


async def _nodes(engine: Any) -> dict[tuple[str, str], Any]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MEMORY_NODE))).all()
    return {(row.kind, row.source_id): row for row in rows}


async def _store_vector(engine: Any, mid: int) -> None:
    async with engine.begin() as conn:
        items = await memory_index.embed_pick(conn, limit=100, now_ms=0)
        item = next(i for i in items if (i.kind, i.source_id) == ("message", str(mid)))
        assert await memory_index.embed_store(
            conn, item, vector=_vector(), model=MODEL, now_ms=1, by_bytes=False,
        )


async def test_an_edit_clears_the_vector_and_an_unchanged_reprojection_keeps_it(engine):
    store = ProjectingRecordStore(engine)
    mid = await store.append(message=_message("the lantern festival plan"))
    key = ("message", str(mid))
    await _store_vector(engine, mid)
    first = (await _nodes(engine))[key]
    assert first.embedding is not None and first.embedding_model == MODEL

    await store.set_person_id(row_id=mid, person_id="member-a")
    unchanged = (await _nodes(engine))[key]
    assert unchanged.embedding is not None and unchanged.embedding_model == MODEL

    await store.append_edit(message_id=mid, message=_message("the lantern plan, moved", ts=2000))
    changed = (await _nodes(engine))[key]
    assert (changed.embedding, changed.embedding_model, changed.embedded_at) == (None, None, None)


async def test_a_wait_is_owed_from_the_last_input_change_never_from_a_reprojection(engine):
    store = ProjectingRecordStore(engine)
    mid = await store.append(message=_message("the lantern festival plan"))
    key = ("message", str(mid))
    where = (MEMORY_NODE.c.kind == "message", MEMORY_NODE.c.source_id == str(mid))

    async def fresh() -> int:
        async with engine.connect() as conn:
            return (await memory_index.embed_counts(
                conn, MODEL, *where, fresh_seconds=180,
            )).fresh

    assert await fresh() == 1
    async with engine.begin() as conn:
        await conn.execute(sa.update(MEMORY_NODE).where(*where).values(
            owed_since=sa.func.clock_timestamp() - sa.text("interval '1 hour'"),
            projected_at=sa.func.clock_timestamp() - sa.text("interval '1 hour'"),
        ))
    before = (await _nodes(engine))[key]

    await store.set_person_id(row_id=mid, person_id="member-a")
    after = (await _nodes(engine))[key]
    assert after.projected_at > before.projected_at
    assert after.owed_since == before.owed_since
    assert await fresh() == 0

    await store.append_edit(message_id=mid, message=_message("the lantern plan, moved", ts=2000))
    assert (await _nodes(engine))[key].owed_since > before.owed_since
    assert await fresh() == 1


async def test_a_rebuild_keeps_a_vector_whose_input_is_unchanged(engine):
    store = ProjectingRecordStore(engine)
    mid = await store.append(message=_message("the lantern festival plan"))
    await _store_vector(engine, mid)

    await memory_index.rebuild(engine)

    assert (await _nodes(engine))[("message", str(mid))].embedding_model == MODEL
