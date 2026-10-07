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


@pytest.fixture(params=["memory", "sql"])
async def revisions(request: Any) -> AsyncIterator[Any]:
    rows = [("room-a", 1), ("room-b", 2), ("room-c", 3), ("room-a", 4)]
    if request.param == "memory":
        fake = InMemoryCharacterEvidence()
        for key, at in rows:
            fake.add_revision(key=key, written_at=at)
        yield fake
        return
    from nora.infra.sql.culture import SqlCultureStore

    engine = create_sql_engine(request.getfixturevalue("_db"))
    try:
        culture = SqlCultureStore(engine)
        for key, at in rows:
            await culture.append(key=key, body=f"{key} {at}", written_at=at,
                                 established_by="test")
        yield SqlCharacterEvidence(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate culture, culture_revision cascade"))
        await engine.dispose()


async def test_a_units_evidence_is_its_rooms_revisions_counted_in_write_order(revisions):
    keys = ["room-a", "room-b"]
    assert await revisions.revision_count(keys=keys) == 3
    assert await revisions.revision_count(keys=[]) == 0
    got, cursor = await revisions.revisions_after(keys=keys, after=1, limit=5)
    assert got == ["room-b", "room-a"] and cursor == 3
    newest, cursor = await revisions.revisions_after(keys=keys, after=0, limit=1)
    assert newest == ["room-a"] and cursor == 3
    assert await revisions.revisions_after(keys=keys, after=3, limit=5) == ([], 3)


@pytest.fixture(params=["memory", "sql"])
async def episodes(request: Any) -> AsyncIterator[tuple[Any, dict[str, int]]]:
    """Three of her sends in `friends` (one still open, one whose reply came
    after the window), one in another unit, and one in a superseded build."""
    from nora.domain.culture import Episode

    if request.param == "memory":
        fake = InMemoryCharacterEvidence()
        fake.add_episode(unit="friends", episode=Episode(
            send_id=1, room="room-a", text="dinner?", outcome="responded",
            reactions=1, edits=0, replies=()))
        fake.add_episode(unit="friends", episode=Episode(
            send_id=3, room="room-a", text="a list", outcome="unanswered",
            reactions=0, edits=0))
        fake.add_episode(unit="friends", open_=True, episode=Episode(
            send_id=5, room="room-a", text="open", outcome="open", reactions=0, edits=0))
        fake.add_episode(unit="wedding", episode=Episode(
            send_id=6, room="room-w", text="w", outcome="responded", reactions=0, edits=0))
        yield fake, {"dinner": 1, "list": 3, "reply": 2}
        return
    from nora.infra.sql.schema import ROLLUP_BUILD, ROLLUP_RESPONSE

    engine = create_sql_engine(request.getfixturevalue("_db"))
    ids: dict[str, int] = {}
    try:
        async with engine.begin() as conn:
            async def msg(name, room, ts, body, nora, quoted=None):
                ids[name] = int((await conn.execute(sa.insert(MESSAGES).values(
                    platform="signal", conversation_id=room, conversation_type="group",
                    sender_id="+85200000000", sender_name="x", ts=ts, body=body,
                    is_nora=int(nora), person_id=None if nora else "ada",
                    quoted_ts=quoted,
                ).returning(MESSAGES.c.id))).scalar_one())
            await msg("dinner", "room-a", 1000, "dinner?", True)
            await msg("reply", "room-a", 1500, "yes", False, quoted=1000)
            await msg("list", "room-a", 2000, "a list", True)
            await msg("late", "room-a", 99_000, "too late", False, quoted=2000)
            await msg("open", "room-a", 3000, "open", True)
            await msg("w", "room-w", 3500, "w", True)
            old = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="v0", params_sha256="p", status="superseded",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()
            cur = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="v1", params_sha256="p", status="current",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()

            async def resp(build, unit, name, ts, outcome, reactions=0):
                await conn.execute(sa.insert(ROLLUP_RESPONSE).values(
                    build_id=build, unit=unit, lineage=[], valid_from_ms=ts,
                    valid_to_ms=ts + 3600, as_of_ms=ts + 3600, message_id=ids[name],
                    platform="signal", conversation_id="room-a", sent_ts=ts,
                    replies=0, reactions=reactions, edits=0, outcome=outcome,
                ))
            await resp(cur, "friends", "dinner", 1000, "responded", reactions=1)
            await resp(cur, "friends", "list", 2000, "unanswered")
            await resp(cur, "friends", "open", 3000, "open")
            await resp(cur, "wedding", "w", 3500, "responded")
            await resp(old, "friends", "w", 3500, "responded")
        yield SqlCharacterEvidence(engine), ids
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate messages, rollup_response, rollup_build cascade"))
        await engine.dispose()


async def test_nora_reads_closed_episodes_of_the_current_build_with_their_replies(episodes):
    store, ids = episodes
    assert await store.episode_count(unit="friends", after=0) == 2
    assert await store.episode_count(unit="friends", after=ids["dinner"]) == 1
    got, cursor = await store.episodes(unit="friends", after=0, limit=5)
    assert [(e.send_id, e.text, e.outcome) for e in got] == [
        (ids["dinner"], "dinner?", "responded"), (ids["list"], "a list", "unanswered"),
    ]
    assert cursor == ids["list"]
    if isinstance(store, SqlCharacterEvidence):
        assert [r.body for r in got[0].replies] == ["yes"]
        assert got[1].replies == ()
    newest, _ = await store.episodes(unit="friends", after=0, limit=1)
    assert [e.send_id for e in newest] == [ids["list"]]
