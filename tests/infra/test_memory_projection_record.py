"""The Record's projection into the memory index (design/unified-memory/
60-build-step-3.md S1): every message write keeps its node, its attachments'
nodes and their edges, in the store's own transaction; a projection failure
queues and never costs the message.

Module-scoped database per `test_withdrawal.py`'s pattern (one migrated
database, truncated between tests)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.media import MediaBlob
from nora.domain.models import DeliveryStatus, NormalizedMessage
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import (
    MEMORY_EDGE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    MESSAGES,
)
from nora.obs.metrics import Metrics

pytestmark = pytest.mark.asyncio

_SHA = "ab" * 32
_ROOM = "signal:g1"


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_projection_" + uuid4().hex
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
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            # TRUNCATE on a HISTORY_TABLES table needs capture off
            # (`test_withdrawal.py`'s reset).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "messages", "media_objects", "row_history", "memory_node",
                "memory_edge", "memory_projection_queue",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


def _message(
    *, ts: int = 1000, body: str = "the lantern festival plan",
    blobs: tuple[MediaBlob, ...] = (),
) -> NormalizedMessage:
    return NormalizedMessage(
        "signal", "g1", "group", "+10000000001", "Member A", ts, body, {},
        media_blobs=blobs,
    )


async def _nodes(engine: Any) -> dict[tuple[str, str], Any]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MEMORY_NODE))).all()
    return {(r.kind, r.source_id): r for r in rows}


async def _edges(engine: Any) -> set[tuple[str, str, str, str, str]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.rel,
            MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id,
        ))).all()
    return {tuple(r) for r in rows}


async def test_an_ingested_message_has_its_node_and_edges(engine):
    metrics = Metrics()
    store = SqlRecordStore(engine, metrics=metrics)
    mid = await store.append(message=_message(), person_id="member-a")
    sid = str(mid)

    nodes = await _nodes(engine)
    node = nodes[("message", sid)]
    assert (node.text, node.origin_room, node.at_ms, node.state) == (
        "the lantern festival plan", _ROOM, 1000, "live",
    )
    assert node.persons == [], "a message is scoped by its room, not by persons"
    assert ("room", _ROOM) in nodes and ("person", "member-a") in nodes
    assert await _edges(engine) == {
        ("message", sid, "in_room", "room", _ROOM),
        ("message", sid, "authored_by", "person", "member-a"),
    }
    assert metrics.get(
        "nora_memory_projection_total", kind="message", outcome="ok",
    ) == 1


async def test_attribution_and_the_platform_timestamp_reproject(engine):
    """An unattributed row has no author edge until lazy adoption names its
    person; a corrected platform timestamp moves the node's time, and a
    status-only close projects nothing."""
    metrics = Metrics()
    store = SqlRecordStore(engine, metrics=metrics)
    mid = await store.append(message=_message())
    sid = str(mid)
    assert ("message", sid, "authored_by", "person", "member-a") not in await _edges(engine)

    await store.set_person_id(row_id=mid, person_id="member-a")
    assert ("message", sid, "authored_by", "person", "member-a") in await _edges(engine)

    await store.set_delivery_outcome(row_id=mid, status=DeliveryStatus.SENT)
    await store.set_delivery_outcome(row_id=mid, status=DeliveryStatus.SENT, ts=1008)
    assert (await _nodes(engine))[("message", sid)].at_ms == 1008
    assert metrics.get(
        "nora_memory_projection_total", kind="message", outcome="ok",
    ) == 3, "append, attribution and the timestamp; not the status-only close"


async def test_her_own_message_is_authored_by_nora(engine):
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message(), is_nora=True)
    assert ("message", str(mid), "authored_by", "nora", "nora") in await _edges(engine)


async def test_an_edit_replaces_the_nodes_text(engine):
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message(), person_id="member-a")
    await store.append_edit(
        message_id=mid, message=_message(ts=2000, body="the lantern festival plan, moved"),
    )
    node = (await _nodes(engine))[("message", str(mid))]
    assert node.text == "the lantern festival plan, moved"
    assert node.at_ms == 1000, "an edit changes what was said, not when"


async def test_a_hidden_message_leaves_with_its_attachments(engine):
    store = SqlRecordStore(engine)
    blob = MediaBlob(sha256=_SHA, media_type="image/jpeg", size=10)
    mid = await store.append(message=_message(blobs=(blob,)), person_id="member-a")
    keep = await store.append(message=_message(ts=1100), person_id="member-a")
    assert ("media", f"{mid}:{_SHA}") in await _nodes(engine)

    assert await store.hide_message(message_id=mid, now_ms=9000) is True

    nodes = await _nodes(engine)
    assert ("message", str(mid)) not in nodes
    assert ("media", f"{mid}:{_SHA}") not in nodes
    assert ("message", str(keep)) in nodes, "positive control: the other message stays"
    assert not {e for e in await _edges(engine) if str(mid) in (e[1], e[4])
                or e[1].startswith(f"{mid}:")}
    async with engine.connect() as conn:
        held = (await conn.execute(
            sa.select(MESSAGES.c.hidden_at).where(MESSAGES.c.id == mid)
        )).scalar_one()
    assert held == 9000, "the hide itself stands"


async def test_a_pruned_message_leaves_the_index(engine):
    store = SqlRecordStore(engine, max_per_conversation=1)
    old = await store.append(message=_message(ts=1000), person_id="member-a")
    new = await store.append(message=_message(ts=2000), person_id="member-a")

    assert await store.prune_all() == 1

    nodes = await _nodes(engine)
    assert ("message", str(old)) not in nodes
    assert ("message", str(new)) in nodes
    assert all(str(old) not in (e[1], e[4]) for e in await _edges(engine))


async def test_a_reading_landing_after_ingest_reaches_the_attachment(engine):
    store = SqlRecordStore(engine)
    media = SqlMediaObjectStore(engine)
    blob = MediaBlob(sha256=_SHA, media_type="image/jpeg", size=10)
    mid = await store.append(message=_message(blobs=(blob,)), person_id="member-a")
    att = f"{mid}:{_SHA}"
    node = (await _nodes(engine))[("media", att)]
    assert (node.text, node.sha256, node.origin_room) == ("", _SHA, _ROOM)
    assert {
        ("media", att, "attached_to", "message", str(mid)),
        ("media", att, "shared_by", "person", "member-a"),
    } <= await _edges(engine)

    assert await media.comprehend(
        sha256=_SHA, title="Lantern", description="A red lantern.",
        content="", model="m",
    ) is True

    assert (await _nodes(engine))[("media", att)].text == "Lantern\nA red lantern."


async def test_a_reading_landing_before_ingest_is_copied_in(engine):
    media = SqlMediaObjectStore(engine)
    assert await media.comprehend(
        sha256=_SHA, title="Lantern", description="A red lantern.",
        content="FESTIVAL", model="m",
    ) is True
    store = SqlRecordStore(engine)
    blob = MediaBlob(sha256=_SHA, media_type="image/jpeg", size=10)
    mid = await store.append(message=_message(blobs=(blob,)), person_id="member-a")

    node = (await _nodes(engine))[("media", f"{mid}:{_SHA}")]
    assert node.text == "Lantern\nA red lantern.\nFESTIVAL"


async def test_a_failed_projection_queues_and_keeps_the_message(engine, monkeypatch):
    def _broken(conn: Any, source_id: str) -> Any:
        raise RuntimeError("projector down")

    monkeypatch.setitem(memory_index._PROJECTORS, "message", _broken)
    metrics = Metrics()
    store = SqlRecordStore(engine, metrics=metrics)

    mid = await store.append(message=_message(), person_id="member-a")

    async with engine.connect() as conn:
        body = (await conn.execute(
            sa.select(MESSAGES.c.body).where(MESSAGES.c.id == mid)
        )).scalar_one()
        queued = (await conn.execute(sa.select(
            MEMORY_PROJECTION_QUEUE.c.kind, MEMORY_PROJECTION_QUEUE.c.source_id,
            MEMORY_PROJECTION_QUEUE.c.attempts, MEMORY_PROJECTION_QUEUE.c.last_error,
        ))).all()
    assert body == "the lantern festival plan", "the message is written"
    assert ("message", str(mid)) not in await _nodes(engine)
    assert [tuple(r) for r in queued] == [("message", str(mid), 1, "RuntimeError")]
    assert metrics.get(
        "nora_memory_projection_total", kind="message", outcome="queued",
    ) == 1
    assert metrics.get(
        "nora_memory_projection_total", kind="message", outcome="ok",
    ) == 0
