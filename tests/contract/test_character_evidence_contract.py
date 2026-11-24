"""Shared contract for SQL and in-memory Cognition Lessons evidence."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MESSAGES
from nora.testing import InMemoryCharacterEvidence
from tests.infra.rollup_fixtures import insert_rollup_row

ROOMS = [("signal", "room-a"), ("discord", "room-b")]


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


@pytest.fixture(params=["memory", "sql"])
async def episodes(request: Any) -> AsyncIterator[tuple[Any, dict[str, int]]]:
    """Six of her sends in `friends` (one `responded`, one `silent`, one
    `unanswered` because none was expected, one still open, one `unknown`
    because the platform was not proved up, one whose reply came after the
    window), one in another unit, and one in a superseded build."""
    from nora.domain.culture import Episode

    if request.param == "memory":
        fake = InMemoryCharacterEvidence()
        fake.add_episode(unit="friends", episode=Episode(
            send_id=1, room="room-a", text="dinner?", outcome="responded",
            reactions=1, edits=0, replies=()))
        fake.add_episode(unit="friends", episode=Episode(
            send_id=3, room="room-a", text="a list", outcome="unanswered",
            reactions=0, edits=0))
        fake.add_episode(unit="friends", episode=Episode(
            send_id=4, room="room-a", text="unk", outcome="unknown", reactions=0, edits=0))
        fake.add_episode(unit="friends", open_=True, episode=Episode(
            send_id=5, room="room-a", text="open", outcome="open", reactions=0, edits=0))
        fake.add_episode(unit="wedding", episode=Episode(
            send_id=6, room="room-w", text="w", outcome="responded", reactions=0, edits=0))
        fake.add_episode(unit="friends", episode=Episode(
            send_id=7, room="room-a", text="who is free?", outcome="silent",
            reactions=0, edits=0))
        fake.add_episode(unit="friends", episode=Episode(
            send_id=8, room="room-a", text="follow only", outcome="responded",
            reactions=0, edits=0, follows=2))
        fake.add_episode(unit="friends", episode=Episode(
            send_id=9, room="room-a", text="follow and reaction", outcome="responded",
            reactions=1, edits=0, follows=1))
        yield fake, {"dinner": 1, "list": 3, "reply": 2, "ask": 7, "fonly": 8, "fmix": 9}
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
            await msg("unk", "room-a", 2500, "unk", True)
            await msg("open", "room-a", 3000, "open", True)
            await msg("w", "room-w", 3500, "w", True)
            await msg("ask", "room-a", 3600, "who is free?", True)
            await msg("fonly", "room-a", 3700, "follow only", True)
            await msg("fmix", "room-a", 3800, "follow and reaction", True)
            old = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="v0", params_sha256="p", status="superseded",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()
            cur = (await conn.execute(sa.insert(ROLLUP_BUILD).values(
                code_version="v1", params_sha256="p", status="current",
            ).returning(ROLLUP_BUILD.c.id))).scalar_one()

            async def resp(build, unit, name, ts, outcome, reactions=0, follows=0):
                await insert_rollup_row(conn, ROLLUP_RESPONSE,
                    build_id=build, unit=unit, build_lineage=[], valid_from_ms=ts,
                    valid_to_ms=ts + 3600, as_of_ms=ts + 3600, message_id=ids[name],
                    platform="signal", conversation_id="room-a", sent_ts=ts,
                    replies=0, reactions=reactions, edits=0, follows=follows, outcome=outcome,
                )
            await resp(cur, "friends", "dinner", 1000, "responded", reactions=1)
            await resp(cur, "friends", "list", 2000, "unanswered")
            await resp(cur, "friends", "unk", 2500, "unknown")
            await resp(cur, "friends", "open", 3000, "open")
            await resp(cur, "friends", "ask", 3600, "silent")
            await resp(cur, "friends", "fonly", 3700, "responded", follows=2)
            await resp(cur, "friends", "fmix", 3800, "responded", reactions=1, follows=1)
            await resp(cur, "wedding", "w", 3500, "responded")
            await resp(old, "friends", "w", 3500, "responded")
        yield SqlCharacterEvidence(engine), ids
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text(
                "truncate messages, rollup_response, rollup_build cascade"))
        await engine.dispose()


async def test_lessons_reads_every_closed_episode(episodes):
    """Lessons reads every CLOSED episode of the current build:
    `unanswered` and follow-only ones too, since the session judges from her
    send's text whether an unanswered one asked something. Still never `open`
    (its outcome can change), `unknown` (an outage, R-27), another unit's or a
    superseded build's."""
    store, ids = episodes
    closed = ["dinner", "list", "ask", "fonly", "fmix"]
    got, cursor = await store.episodes(
        unit="friends", after=0, limit=10)
    assert [e.send_id for e in got] == [ids[n] for n in closed]
    assert [e.outcome for e in got] == [
        "responded", "unanswered", "silent", "responded", "responded"]
    assert got[1].text == "a list" and got[3].follows == 2
    assert cursor == ids["fmix"]
    if isinstance(store, SqlCharacterEvidence):
        assert [r.body for r in got[0].replies] == ["yes"]
        assert got[1].replies == ()
    newest, cursor = await store.episodes(unit="friends", after=0, limit=2)
    assert [e.send_id for e in newest] == [ids["fonly"], ids["fmix"]]
    assert await store.episodes(unit="friends", after=cursor, limit=2) == ([], cursor)


@pytest.fixture(params=["memory", "sql"])
async def addressed(request: Any) -> AsyncIterator[Any]:
    """Room-a: her message at ts 1000; a reply quoting it (2000); a message
    naming her (3000); one quoting a non-her message (3500); a plain one (4000);
    a hidden one naming her (4500); her own message naming herself (4600).
    Room-b (another room) names her (5000)."""
    rows = [
        # id, room, ts, body, is_nora, quoted_ts, hidden
        (1, "room-a", 1000, "here are five", True, None, False),
        (2, "room-a", 2000, "stop it", False, 1000, False),
        (3, "room-a", 3000, "NORA too long", False, None, False),
        (4, "room-a", 3500, "quoting a friend", False, 2000, False),
        (5, "room-a", 4000, "plain", False, None, False),
        (6, "room-a", 4500, "nora hidden", False, None, True),
        (7, "room-a", 4600, "I am Nora", True, None, False),
        (8, "room-b", 5000, "nora elsewhere", False, None, False),
        (9, "room-a", 5100, "\ufffc stop the gifs", False, None, False),
        (10, "room-a", 5200, "pure ignorance, anorak", False, None, False),
    ]
    mentions = {9: frozenset({"her-uuid"})}
    if request.param == "memory":
        fake = InMemoryCharacterEvidence()
        for i, room, ts, body, nora, quoted, hidden in rows:
            fake.add_message(id=i, platform="signal", room=room, person_id=None if nora else "ada",
                             body=body, ts=ts, is_nora=nora, quoted_ts=quoted, hidden=hidden,
                             mentions=mentions.get(i, frozenset()))
        fake.set_last_run(adaptor="lessons", key="friends", at_ms=1_234_000)
        yield fake
        return
    from nora.infra.sql.schema import CONSOLIDATION_WATERMARK

    engine = create_sql_engine(request.getfixturevalue("_db"))
    try:
        async with engine.begin() as conn:
            for i, room, ts, body, nora, quoted, hidden in rows:
                await conn.execute(sa.insert(MESSAGES).values(
                    platform="signal", conversation_id=room, conversation_type="group",
                    sender_id="+85200000000", sender_name="x", ts=ts, body=body,
                    is_nora=int(nora), person_id=None if nora else "ada", quoted_ts=quoted,
                    hidden_at=ts if hidden else None,
                    mentions_json=(json.dumps(sorted(mentions[i]), separators=(", ", ": "))
                                   if i in mentions else None),
                ))
            await conn.execute(sa.insert(CONSOLIDATION_WATERMARK).values(
                consolidator_name="lessons", scope_id="friends", last_message_id=0,
                updated_at=sa.func.to_timestamp(1234),
            ))
        yield SqlCharacterEvidence(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate messages, consolidation_watermark cascade"))
        await engine.dispose()


async def test_addressed_messages_quote_her_or_name_her_in_the_rooms_asked(addressed):
    """L4: what may be a correction. A reply quoting HER message, or one naming
    her in any case, in the rooms asked, in the time window; never her own, a
    hidden one, a reply quoting someone else, or another room's."""
    got = await addressed.addressed_since(
        rooms=[("signal", "room-a")], since_ms=0, until_ms=10_000, limit=10,
        self_ids=frozenset({"her-uuid"}))
    assert [m.row.body for m in got] == ["stop it", "NORA too long", "\ufffc stop the gifs"]
    # Without her ids an @-mention (U+FFFC in the body) is invisible, and a word
    # that merely contains «nora» never addresses her.
    plain = await addressed.addressed_since(
        rooms=[("signal", "room-a")], since_ms=0, until_ms=10_000, limit=10)
    assert [m.row.body for m in plain] == ["stop it", "NORA too long"]
    assert all(m.room == "room-a" for m in got)
    window = await addressed.addressed_since(
        rooms=[("signal", "room-a")], since_ms=2000, until_ms=5000, limit=10)
    assert [m.row.body for m in window] == ["NORA too long"]
    newest = await addressed.addressed_since(
        rooms=[("signal", "room-a")], since_ms=0, until_ms=5000, limit=1)
    assert [m.row.body for m in newest] == ["NORA too long"]
    assert await addressed.last_run_ms(adaptor="lessons", key="friends") == 1_234_000
    assert await addressed.last_run_ms(adaptor="lessons", key="other") is None



async def test_last_run_reads_the_consolidation_watermark_timestamp(_db):
    """The correction fast path's lower bound is the `updated_at` that
    `advance_watermark` sets on every run that is not `unavailable`; if the read
    drifted to another column, a correction would wake the unit every tick."""
    from nora.infra.sql.language import SqlLanguageStore

    engine = create_sql_engine(_db)
    try:
        before = await SqlCharacterEvidence(engine).last_run_ms(adaptor="lessons", key="u1")
        await SqlLanguageStore(engine).advance_watermark(
            consolidator_name="lessons", scope_id="u1", last_message_id=0)
        after = await SqlCharacterEvidence(engine).last_run_ms(adaptor="lessons", key="u1")
        assert before is None and after is not None and after > 1_700_000_000_000
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate consolidation_watermark"))
        await engine.dispose()
