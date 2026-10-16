"""The Record's projection into the memory index: every message write keeps its
node, its attachments' nodes and their edges, in the store's own transaction; a
projection failure queues and never costs the message.

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
    ROLLUP_BUILD,
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
                "memory_edge", "memory_projection_queue", "rollup_build",
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
    # An edge INTO the message that another node owns (a memo's citation).
    cites = ("memo", "memo-1", "cites", "message", str(mid))
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MEMORY_EDGE).values(
            src_kind="memo", src_id="memo-1", rel="cites", dst_kind="message",
            dst_id=str(mid), provenance="memo_revision.citations",
        ))

    assert await store.hide_message(message_id=mid, now_ms=9000) is True

    nodes = await _nodes(engine)
    assert ("message", str(mid)) not in nodes
    assert ("media", f"{mid}:{_SHA}") not in nodes
    assert ("message", str(keep)) in nodes, "positive control: the other message stays"
    edges = await _edges(engine)
    # A delete removes the edges the node owns (out of it and out of its
    # attachments); the memo's citation is the memo's, and stays dangling,
    # exactly as a rebuild writes it (it drops itself at a read's join).
    assert not {e for e in edges if e[1] == str(mid) or e[1].startswith(f"{mid}:")}
    assert {e for e in edges if e[4] == str(mid)} == {cites}
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


async def _queue_rows(engine: Any) -> list[tuple[Any, ...]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_PROJECTION_QUEUE.c.kind, MEMORY_PROJECTION_QUEUE.c.source_id,
            MEMORY_PROJECTION_QUEUE.c.attempts, MEMORY_PROJECTION_QUEUE.c.last_error,
        ))).all()
    return [tuple(r) for r in rows]


async def test_a_failure_inside_postgres_queues_and_the_transaction_survives(
    engine, monkeypatch,
):
    """The failure a real projector meets: Postgres refuses a statement (here
    a CHECK), which aborts the transaction unless the savepoint contains it.
    The message still commits, the queue row is written after the refusal on
    the same connection, and the store keeps working."""
    async def _refused(conn: Any, source_id: str) -> None:
        await conn.execute(sa.insert(MEMORY_NODE).values(
            kind="not-a-kind", source_id=source_id, origin_room=_ROOM,
        ))

    monkeypatch.setitem(memory_index._PROJECTORS, "message", _refused)
    metrics = Metrics()
    store = SqlRecordStore(engine, metrics=metrics)

    mid = await store.append(message=_message(), person_id="member-a")

    async with engine.connect() as conn:
        assert (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGES).where(MESSAGES.c.id == mid)
        )).scalar_one() == 1, "the message committed"
    assert await _queue_rows(engine) == [("message", str(mid), 1, "IntegrityError")]
    assert metrics.get("nora_memory_projection_total", outcome="queued") == 1
    assert ("message", str(mid)) not in await _nodes(engine)

    monkeypatch.undo()
    later = await store.append(message=_message(ts=2000), person_id="member-a")
    assert ("message", str(later)) in await _nodes(engine), "the store still projects"


async def test_a_projection_that_lands_clears_its_queue_row(engine, monkeypatch):
    def _broken(conn: Any, source_id: str) -> Any:
        raise RuntimeError("projector down")

    monkeypatch.setitem(memory_index._PROJECTORS, "message", _broken)
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message())
    await store.set_person_id(row_id=mid, person_id="member-a")
    assert await _queue_rows(engine) == [("message", str(mid), 2, "RuntimeError")], (
        "a second failure of the same source counts an attempt, not a row"
    )

    monkeypatch.undo()
    await store.set_person_id(row_id=mid, person_id="member-a")

    assert await _queue_rows(engine) == []
    assert ("message", str(mid)) in await _nodes(engine)


async def test_a_failed_drop_of_any_size_queues_every_id(engine, monkeypatch):
    """A retention prune drops every message past the window in one call. A
    VALUES row per id would bind three parameters each and pass asyncpg's
    32,767 at ~10,900 ids, losing the queue and leaving pruned text
    searchable."""
    async def _broken(conn: Any, kind: str, ids: list[str]) -> None:
        raise RuntimeError("projector down")

    monkeypatch.setattr(memory_index, "_drop", _broken)
    metrics = Metrics()
    ids = [str(i) for i in range(1, 20_001)]
    async with engine.begin() as conn:
        await memory_index.drop(conn, "message", ids, metrics=metrics)

    assert metrics.get("nora_memory_projection_total", outcome="queued") == 1
    assert metrics.get("nora_memory_projection_total", outcome="lost") == 0
    async with engine.connect() as conn:
        held = (await conn.execute(
            sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE)
            .where(MEMORY_PROJECTION_QUEUE.c.kind == "message")
        )).scalar_one()
    assert held == 20_000


async def _drain(engine: Any, metrics: Any = None) -> int:
    async with engine.begin() as conn:
        return await memory_index.drain_queue(conn, metrics=metrics)


async def test_a_drain_reprojects_what_was_queued_and_is_idempotent(engine, monkeypatch):
    """The analytics run drains the queue once per tick. A drain that
    lands clears the row; one that fails again counts an attempt and keeps it;
    a second drain over an empty queue does nothing."""
    def _broken(conn: Any, source_id: str) -> Any:
        raise RuntimeError("projector down")

    monkeypatch.setitem(memory_index._PROJECTORS, "message", _broken)
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message(), person_id="member-a")
    assert await _drain(engine) == 1
    assert await _queue_rows(engine) == [("message", str(mid), 2, "RuntimeError")]

    monkeypatch.undo()
    metrics = Metrics()
    assert await _drain(engine, metrics) == 1
    assert await _queue_rows(engine) == []
    assert ("message", str(mid)) in await _nodes(engine)
    assert metrics.get("nora_memory_projection_total", kind="message", outcome="ok") == 1
    assert await _drain(engine) == 0


async def test_a_drained_episode_the_current_build_no_longer_holds_is_dropped(engine):
    """A queued outing whose key the current build does not hold leaves the
    index, its own edges with it. With NO current build it is neither
    projected nor dropped: it stays queued, counted an attempt."""
    key = "friends|a,b|2026-09-21T19:00:00+08:00/2026-09-21T22:00:00+08:00"
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MEMORY_NODE).values(
            kind="outing", source_id=key, unit="friends", persons=["a", "b"], text="outing",
        ))
        await conn.execute(sa.insert(MEMORY_EDGE).values(
            src_kind="outing", src_id=key, rel="about", dst_kind="person", dst_id="a",
            provenance="rollup_outing.persons",
        ))
        await conn.execute(sa.insert(MEMORY_PROJECTION_QUEUE).values(
            kind="outing", source_id=key, last_error="RuntimeError",
        ))
    assert await _drain(engine) == 1
    assert ("outing", key) in await _nodes(engine), "no build: never read as «not held»"
    assert await _queue_rows(engine) == [("outing", key, 2, "_NoCurrentBuildError")]

    async with engine.begin() as conn:
        await conn.execute(sa.insert(ROLLUP_BUILD).values(
            code_version="t", params_sha256="", status="current",
        ))
    assert await _drain(engine) == 1
    assert ("outing", key) not in await _nodes(engine)
    assert await _edges(engine) == set()
    assert await _queue_rows(engine) == []


async def test_rows_that_keep_failing_never_starve_a_newer_one(engine, monkeypatch):
    """The least-tried row drains first: stuck rows ahead of it in time, more
    of them than one drain takes, do not keep a fresh failure waiting."""
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MEMORY_PROJECTION_QUEUE), [
            {"kind": "nope", "source_id": str(i), "attempts": 7, "last_error": "KeyError"}
            for i in range(5)
        ])

    def _broken(conn: Any, source_id: str) -> Any:
        raise RuntimeError("projector down")

    monkeypatch.setitem(memory_index._PROJECTORS, "message", _broken)
    mid = await SqlRecordStore(engine).append(message=_message(), person_id="member-a")
    monkeypatch.undo()

    async with engine.begin() as conn:
        assert await memory_index.drain_queue(conn, limit=2) == 2
    assert ("message", str(mid)) in await _nodes(engine)
    assert len(await _queue_rows(engine)) == 5, "the stuck rows stay, visible"
