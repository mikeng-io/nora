"""`SqlParticipationStateStore` — the participation trigger's per-room
watermarks, across processes.

Single-store file, not a contract suite (there is one implementation). The
database is MODULE-scoped per `test_tag_trace_store.py`'s pattern: one
`CREATE DATABASE` plus one migration chain for the whole file, truncated
between tests, because rebuilding the world per test degrades the shared
testcontainer and the cost lands on an unrelated wall-clock test elsewhere.

Written around the two properties that make this table worth existing: an
ABSENT value never renders as a real instant, and the read joins the record
rather than assuming the trigger still remembers it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.participation_state import SqlParticipationStateStore
from nora.infra.sql.schema import MESSAGES

T0 = 1_757_000_000_000
PLATFORM = "signal"
ROOM = "g1"


@pytest.fixture(scope="module")
def _participation_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_participation_" + uuid4().hex
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
async def engine(_participation_db: str) -> AsyncIterator[Any]:
    """The ENGINE is per test (cheap); the DATABASE and its migrations are
    module-scoped."""
    eng = create_sql_engine(_participation_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(
                sa.text("truncate participation_window_state, messages, message_edit, "
                        "message_reaction, message_delete, message_reading_queue")
            )
        await eng.dispose()


@pytest.fixture
def store(engine: Any) -> SqlParticipationStateStore:
    return SqlParticipationStateStore(engine)


async def _message(
    engine: Any, *, ts: int, is_nora: int = 0, room: str = ROOM,
    ctype: str = "group",
) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MESSAGES).values(
            platform=PLATFORM, conversation_id=room, conversation_type=ctype,
            sender_id="+852111", sender_name="Miles", ts=ts, body="hi",
            is_nora=is_nora,
        ))


async def test_a_room_with_no_row_is_absent_not_zero(
    store: SqlParticipationStateStore,
) -> None:
    """The distinction this whole table rests on: a room that has never had a
    window judged produces NO cursor, not one dated 1970."""
    assert await store.read_cursors() == []


async def test_a_window_end_round_trips(store: SqlParticipationStateStore) -> None:
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    [cursor] = await store.read_cursors()
    assert cursor.platform == PLATFORM
    assert cursor.conversation_id == ROOM
    assert cursor.last_window_end_at == T0


async def test_never_sent_reads_as_none_not_epoch_zero(
    store: SqlParticipationStateStore,
) -> None:
    """A room that closed a window but never SENT must not come back as
    «last spoke in 1970» — that answer permits the send too, so the bug would
    survive its own test."""
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_send_at is None


async def test_a_send_round_trips(store: SqlParticipationStateStore) -> None:
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    await store.record_send(platform=PLATFORM, conversation_id=ROOM, at_ms=T0)
    [cursor] = await store.read_cursors()
    assert cursor.last_send_at == T0


async def test_a_later_window_does_not_clear_the_send(
    store: SqlParticipationStateStore,
) -> None:
    """The cooldown outlives the windows that do not spend it: a close that
    ran no turn, or ran a silent one, must leave `last_send_at` alone."""
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    await store.record_send(platform=PLATFORM, conversation_id=ROOM, at_ms=T0)
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0 + 60_000,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_window_end_at == T0 + 60_000
    assert cursor.last_send_at == T0


async def test_a_send_does_not_rewind_the_window_end(
    store: SqlParticipationStateStore,
) -> None:
    """`record_send` supplies `last_window_end_at` for its INSERT branch only.
    On an existing row it must touch nothing but the send."""
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0 + 60_000,
    )
    await store.record_send(platform=PLATFORM, conversation_id=ROOM, at_ms=T0)
    [cursor] = await store.read_cursors()
    assert cursor.last_window_end_at == T0 + 60_000


async def test_a_second_window_upserts_rather_than_raising(
    store: SqlParticipationStateStore,
) -> None:
    """The second window is the ordinary case; a plain INSERT would raise."""
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0 + 1,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_window_end_at == T0 + 1


async def test_the_read_joins_the_newest_inbound_message(
    store: SqlParticipationStateStore, engine: Any,
) -> None:
    """The cursor is a JOIN of two facts — where the room was judged, and where
    the record has got to since. `_last_message_at` is re-derived here because
    it moves on the dispatcher's synchronous path, which may not await."""
    await _message(engine, ts=T0 - 10_000, ctype="group")
    await _message(engine, ts=T0 - 5_000, ctype="group")
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_message_at == T0 - 5_000
    assert cursor.conversation_type == "group"


async def test_noras_own_rows_are_never_the_newest_message(
    store: SqlParticipationStateStore, engine: Any,
) -> None:
    """Her replies are rows in `messages` and are NOT participation
    candidates. Counting one would place the newest message AFTER the window
    she spoke in, so every turn would re-arm the next one."""
    await _message(engine, ts=T0 - 5_000)
    await _message(engine, ts=T0 + 5_000, is_nora=1)
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_message_at == T0 - 5_000


async def test_a_room_whose_messages_are_gone_keeps_its_watermark(
    store: SqlParticipationStateStore,
) -> None:
    """A LEFT join, not an inner one: losing the row to retention would make
    the next window the room closes look like its first."""
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_window_end_at == T0
    assert cursor.last_message_at is None
    assert cursor.conversation_type is None


async def test_another_rooms_messages_are_not_this_rooms(
    store: SqlParticipationStateStore, engine: Any,
) -> None:
    await _message(engine, ts=T0 + 90_000, room="g2")
    await _message(engine, ts=T0 - 5_000, room=ROOM)
    await store.record_window_end(
        platform=PLATFORM, conversation_id=ROOM, at_ms=T0,
    )
    [cursor] = await store.read_cursors()
    assert cursor.last_message_at == T0 - 5_000


async def test_rooms_are_independent_rows(
    store: SqlParticipationStateStore,
) -> None:
    await store.record_window_end(
        platform=PLATFORM, conversation_id="g1", at_ms=T0,
    )
    await store.record_window_end(
        platform="discord", conversation_id="g1", at_ms=T0 + 7,
    )
    await store.record_send(platform="discord", conversation_id="g1", at_ms=T0 + 7)
    cursors = {(c.platform, c.conversation_id): c for c in await store.read_cursors()}
    assert cursors[(PLATFORM, "g1")].last_send_at is None
    assert cursors[("discord", "g1")].last_send_at == T0 + 7
