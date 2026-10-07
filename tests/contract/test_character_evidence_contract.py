"""`SqlCharacterEvidence` and `InMemoryCharacterEvidence` answer the same
counts and reads over the same Record (design/self-growth/10-character.md §3).

What a Character session is woken by and handed, so each filter here is one a
wrong answer would cost silently: another person's line in someone's account,
Nora's own words counted as theirs, a hidden message read back, a room of
another unit, or a backlog walked from its start instead of read for what was
said lately.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.culture import RoomMessage
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MESSAGES
from nora.testing import InMemoryCharacterEvidence

ROOMS = [("signal", "room-a"), ("discord", "room-b")]


class _Evidence(Protocol):
    async def person_count(self, *, rooms: Any, person_id: str, after: int) -> int: ...
    async def person_since(
        self, *, rooms: Any, person_id: str, after: int, limit: int,
    ) -> tuple[list[RoomMessage], int]: ...


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_character_" + uuid4().hex
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
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


#: (platform, room, person, body, is_nora, hidden), inserted in this order so
#: the Record's ids ascend with the index.
_ROWS = [
    ("signal", "room-a", "ada", "a1", False, False),
    ("signal", "room-a", "bo", "b1", False, False),
    ("discord", "room-b", "ada", "a2", False, False),
    ("signal", "room-a", None, "nora", True, False),
    ("signal", "room-a", "ada", "a3 hidden", False, True),
    ("signal", "room-c", "ada", "a4 other unit", False, False),
    ("discord", "room-b", "ada", "a5", False, False),
]


@pytest.fixture(params=["memory", "sql"])
async def seeded(request: Any) -> AsyncIterator[tuple[_Evidence, list[int]]]:
    if request.param == "memory":
        fake = InMemoryCharacterEvidence()
        for i, (platform, room, person, body, nora, hidden) in enumerate(_ROWS, 1):
            fake.add_message(id=i, platform=platform, room=room, person_id=person,
                             body=body, ts=1000 + i, is_nora=nora, hidden=hidden)
        yield fake, list(range(1, len(_ROWS) + 1))
        return
    engine = create_sql_engine(request.getfixturevalue("_db"))
    ids: list[int] = []
    try:
        async with engine.begin() as conn:
            for i, (platform, room, person, body, nora, hidden) in enumerate(_ROWS, 1):
                ids.append(int((await conn.execute(sa.insert(MESSAGES).values(
                    platform=platform, conversation_id=room, conversation_type="group",
                    sender_id="+85200000000", sender_name="x", ts=1000 + i, body=body,
                    is_nora=int(nora), person_id=person,
                    hidden_at=2_000 if hidden else None,
                ).returning(MESSAGES.c.id))).scalar_one()))
        yield SqlCharacterEvidence(engine), ids
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate messages cascade"))
        await engine.dispose()


async def test_a_person_counts_only_their_own_visible_lines_in_the_units_rooms(seeded):
    store, ids = seeded
    assert await store.person_count(rooms=ROOMS, person_id="ada", after=0) == 3
    assert await store.person_count(rooms=ROOMS, person_id="ada", after=ids[2]) == 1
    assert await store.person_count(rooms=ROOMS, person_id="bo", after=0) == 1
    assert await store.person_count(rooms=[], person_id="ada", after=0) == 0


async def test_the_read_is_the_newest_lines_oldest_first_and_the_cursor_is_the_newest(seeded):
    store, ids = seeded
    got, cursor = await store.person_since(rooms=ROOMS, person_id="ada", after=0, limit=2)
    assert [(m.room, m.row.body) for m in got] == [("room-b", "a2"), ("room-b", "a5")]
    assert [m.row.id for m in got] == [ids[2], ids[6]]
    assert cursor == ids[6]
    nothing, unmoved = await store.person_since(
        rooms=ROOMS, person_id="ada", after=ids[6], limit=5,
    )
    assert nothing == [] and unmoved == ids[6]
