"""`SqlCharacterEvidence` and `InMemoryCharacterEvidence` answer the same
counts and reads over the same Record.

What a Character session is woken by and handed, so each filter here is one a
wrong answer would cost silently: another person's line in someone's account,
Nora's own words counted as theirs, a hidden message read back, a room of
another unit, or a backlog walked from its start instead of read for what was
said lately.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.culture import RoomMessage
from nora.domain.social_stream import to_us
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


async def test_the_head_is_the_newest_message_id_whoever_wrote_it(seeded):
    store, ids = seeded
    assert await store.message_head() == max(ids)


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
                await conn.execute(sa.insert(ROLLUP_RESPONSE).values(
                    build_id=build, unit=unit, lineage=[], valid_from_ms=ts,
                    valid_to_ms=ts + 3600, as_of_ms=ts + 3600, message_id=ids[name],
                    platform="signal", conversation_id="room-a", sent_ts=ts,
                    replies=0, reactions=reactions, edits=0, follows=follows, outcome=outcome,
                ))
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


async def test_nora_counts_and_reads_evidence_episodes_only(episodes):
    """DEC-290 (4): her wake counts closed episodes that are EVIDENCE
    (`Episode.is_evidence`): `responded`, `silent`, or with a reply, reaction or
    edit. An `unanswered` one (none was expected), an `unknown` one (an outage,
    R-27) and an `open` one are neither counted, read, nor the cursor."""
    store, ids = episodes
    assert await store.episode_count(unit="friends", after=0) == 3
    assert await store.episode_count(unit="friends", after=ids["dinner"]) == 2
    assert await store.episode_count(unit="friends", after=ids["ask"]) == 1
    assert await store.episode_count(unit="friends", after=ids["fmix"]) == 0
    got, cursor = await store.episodes(unit="friends", after=0, limit=5)
    assert [(e.send_id, e.text, e.outcome) for e in got] == [
        (ids["dinner"], "dinner?", "responded"),
        (ids["ask"], "who is free?", "silent"),
        (ids["fmix"], "follow and reaction", "responded"),
    ]
    assert cursor == ids["fmix"]
    if isinstance(store, SqlCharacterEvidence):
        assert [r.body for r in got[0].replies] == ["yes"]
        assert got[1].replies == ()
    newest, _ = await store.episodes(unit="friends", after=0, limit=1)
    assert [e.send_id for e in newest] == [ids["fmix"]]


async def test_an_episode_answered_only_by_follows_is_not_evidence_in_either_store(episodes):
    """The session is not shown the follow messages, so `follows` alone is not
    counted, not read and does not move the cursor; beside a reaction it is."""
    store, ids = episodes
    got, _ = await store.episodes(unit="friends", after=0, limit=10)
    assert ids["fonly"] not in [e.send_id for e in got]
    only, cursor = await store.episodes(unit="friends", after=ids["ask"], limit=10)
    assert [e.send_id for e in only] == [ids["fmix"]] and cursor == ids["fmix"]


async def test_lessons_count_and_read_every_closed_episode(episodes):
    """Lessons reads every CLOSED episode of the current build:
    `unanswered` and follow-only ones too, since the session judges from her
    send's text whether an unanswered one asked something. Still never `open`
    (its outcome can change), `unknown` (an outage, R-27), another unit's or a
    superseded build's."""
    store, ids = episodes
    closed = ["dinner", "list", "ask", "fonly", "fmix"]
    assert await store.episode_count(unit="friends", after=0, evidence_only=False) == 5
    assert await store.episode_count(
        unit="friends", after=ids["ask"], evidence_only=False) == 2
    got, cursor = await store.episodes(
        unit="friends", after=0, limit=10, evidence_only=False)
    assert [e.send_id for e in got] == [ids[n] for n in closed]
    assert [e.outcome for e in got] == [
        "responded", "unanswered", "silent", "responded", "responded"]
    assert got[1].text == "a list" and got[3].follows == 2
    assert cursor == ids["fmix"]
    newest, _ = await store.episodes(unit="friends", after=0, limit=2, evidence_only=False)
    assert [e.send_id for e in newest] == [ids["fonly"], ids["fmix"]]


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



async def test_last_run_is_the_watermark_the_dream_loop_writes(_db):
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


async def test_the_social_cursor_starts_at_zero_moves_forward_only_and_is_its_own_row(_db):
    """A person session's social cursor and its read's time: `(0, 0)` before
    any read settled, moved by `advance_social_cursor`, neither ever back (a
    late settle of an older read must not re-hand what a newer one moved
    past, nor re-count what it counted), and on a row of its own, so the
    key's message watermark is untouched."""
    from nora.infra.sql.language import SqlLanguageStore

    engine = create_sql_engine(_db)
    try:
        for store in (SqlCharacterEvidence(engine), InMemoryCharacterEvidence()):
            assert await store.social_cursor(adaptor="character-person", key="k") == (0, 0)
            # Real values: a `recorded_at` in microseconds, past INTEGER.
            live = to_us(datetime(2026, 10, 17, 9, 30, 15, 123456, tzinfo=UTC))
            read = live + 5_000_000
            assert live > 2**31
            await store.advance_social_cursor(
                adaptor="character-person", key="k", cursor=live, read_at=read)
            await store.advance_social_cursor(
                adaptor="character-person", key="k", cursor=live - 1, read_at=read - 1)
            assert await store.social_cursor(
                adaptor="character-person", key="k") == (live, read)
            assert await store.social_cursor(adaptor="character-person", key="j") == (0, 0)
        assert await SqlLanguageStore(engine).watermark_for(
            consolidator_name="character-person", scope_id="k") == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate consolidation_watermark"))
        await engine.dispose()
