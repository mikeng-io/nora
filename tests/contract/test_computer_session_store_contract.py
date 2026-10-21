"""One suite, both implementations of `ComputerSessionStore` (migration
`0082_computer_sessions`): the Postgres store on a database migrated once per module
and truncated between tests, and the in-memory fake.

What both must hold: a call in a room with no open session opens exactly
one, and a call while one is open continues it (even two at once); expiry
is a compare-and-set on `last_activity_at`, so a call that lands between the
sweeper's read and its write keeps the session open; an expired session is
never continued, the next call opens a new one; nothing is deleted.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.computer_sessions import ComputerSessionStore
from nora.infra.sql.computer_sessions import SqlComputerSessionStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeComputerSessionStore

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SCOPE = "ab" * 16
OTHER = "cd" * 16


@pytest.fixture(scope="module")
def _sessions_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_computer_sessions_" + uuid4().hex
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


@pytest.fixture(params=["fake", "sql"])
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "fake":
        yield FakeComputerSessionStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_sessions_db"))
    try:
        yield SqlComputerSessionStore(engine)
    finally:
        async with engine.begin() as conn:
            # The row-history guard refuses a TRUNCATE unless switched off.
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("TRUNCATE computer_sessions"))
        await engine.dispose()


async def _open(store, *, scope=SCOPE, conversation_id="conv-1", now=T0, objective="fit it"):
    return await store.continue_or_open(
        platform="signal", conversation_id=conversation_id, scope=scope,
        objective=objective, actor="tag", tag_id="tag-1", now=now,
    )


def test_both_satisfy_the_port():
    assert isinstance(FakeComputerSessionStore(), ComputerSessionStore)
    assert isinstance(SqlComputerSessionStore(engine=None), ComputerSessionStore)  # type: ignore[arg-type]


async def test_a_call_in_a_room_with_no_open_session_opens_exactly_one(store):
    session, opened = await _open(store)
    assert opened is True
    assert (session.status, session.objective, session.scope) == ("open", "fit it", SCOPE)
    assert session.created_at == session.last_activity_at == T0
    assert session.created_by_tag == "tag-1" and session.actor == "tag"
    rooms = await store.for_scope(scope=SCOPE, limit=10)
    assert [s.id for s in rooms] == [session.id]


async def test_a_call_while_a_session_is_open_continues_it_not_a_second(store):
    first, _ = await _open(store)
    later = T0 + timedelta(minutes=5)
    again, opened = await _open(store, now=later, objective="a different message")
    assert opened is False
    assert again.id == first.id
    assert again.objective == "fit it"  # the objective is the opener's
    assert again.last_activity_at == later and again.updated_at == later
    assert len(await store.open_sessions(limit=10)) == 1


async def test_two_concurrent_first_calls_share_one_session(store):
    results = await asyncio.gather(*(_open(store) for _ in range(5)))
    assert len({s.id for s, _ in results}) == 1
    assert sum(opened for _, opened in results) == 1


async def test_rooms_have_their_own_sessions(store):
    mine, _ = await _open(store)
    theirs, opened = await _open(store, scope=OTHER, conversation_id="conv-2")
    assert opened is True and theirs.id != mine.id


async def test_expire_is_a_compare_and_set_on_last_activity(store):
    session, _ = await _open(store)
    # A call lands between the sweeper's read and its write.
    await _open(store, now=T0 + timedelta(seconds=1))
    missed = await store.expire(session.id, seen_last_activity_at=T0, reason="idle",
                                now=T0 + timedelta(hours=25))
    assert missed is None
    [still] = await store.open_sessions(limit=10)
    assert still.status == "open"
    expired = await store.expire(session.id, seen_last_activity_at=still.last_activity_at,
                                 reason="idle", now=T0 + timedelta(hours=26))
    assert expired is not None
    assert (expired.status, expired.expired_reason) == ("expired", "idle")
    assert expired.expired_at == T0 + timedelta(hours=26)
    # A second sweep finds nothing to expire.
    assert await store.expire(session.id, seen_last_activity_at=still.last_activity_at,
                              reason="idle", now=T0 + timedelta(hours=27)) is None


async def test_the_next_call_after_an_expiry_opens_a_new_session_and_keeps_the_old(store):
    session, _ = await _open(store)
    await store.expire(session.id, seen_last_activity_at=T0, reason="idle",
                       now=T0 + timedelta(hours=25))
    fresh, opened = await _open(store, now=T0 + timedelta(hours=30), objective="next task")
    assert opened is True and fresh.id != session.id
    rooms = await store.for_scope(scope=SCOPE, limit=10)
    assert [(s.id, s.status) for s in rooms] == [(fresh.id, "open"), (session.id, "expired")]
    assert await store.open_sessions(limit=10) == [fresh]


async def test_open_sessions_lists_the_longest_idle_first(store):
    a, _ = await _open(store, now=T0 + timedelta(hours=2))
    b, _ = await _open(store, scope=OTHER, conversation_id="conv-2", now=T0)
    assert [s.id for s in await store.open_sessions(limit=10)] == [b.id, a.id]


async def test_resources_are_noted_without_moving_activity(store):
    session, _ = await _open(store)
    later = T0 + timedelta(hours=1)
    await store.note_resources(session.id, tmux=["scrape", "scrape", "fit"], now=later)
    await store.note_resources(session.id, tmux=["fit", "plot"], now=later)
    [got] = await store.open_sessions(limit=10)
    assert got.held_tmux == ("scrape", "fit", "plot")
    assert got.last_activity_at == T0
    await store.expire(session.id, seen_last_activity_at=T0, reason="jobs_ended",
                       now=later)
    await store.note_resources(session.id, tmux=["late"], now=later)
    [expired] = await store.for_scope(scope=SCOPE, limit=10)
    assert expired.held_tmux == ("scrape", "fit", "plot")


async def test_a_naive_datetime_is_refused(store):
    with pytest.raises(ValueError):
        await _open(store, now=datetime(2026, 9, 30, 12, 0))


async def test_a_background_caller_only_continues_never_opens(store):
    none = await store.continue_or_open(
        platform="signal", conversation_id="conv-1", scope=SCOPE, objective="",
        actor="dreamer:proactive", tag_id="", now=T0, open_new=False)
    assert none is None and await store.open_sessions(limit=10) == []
    opened, _ = await _open(store)
    later = T0 + timedelta(minutes=1)
    got = await store.continue_or_open(
        platform="signal", conversation_id="conv-1", scope=SCOPE, objective="",
        actor="dreamer:proactive", tag_id="", now=later, open_new=False)
    assert got is not None and got[0].id == opened.id and got[1] is False
    assert got[0].last_activity_at == later


async def test_scope_listing_and_reuse_span_creation_rooms_without_changing_provenance(store):
    session, _ = await _open(store)
    same, opened = await store.continue_or_open(
        platform='discord', conversation_id='discord-room', scope=SCOPE,
        objective='new message', actor='fire', tag_id='tag-2', now=T0 + timedelta(minutes=1))
    other, _ = await _open(store, scope=OTHER, conversation_id='other')
    assert not opened and same.id == session.id
    assert (same.platform, same.conversation_id, same.actor, same.created_by_tag) == (
        'signal', 'conv-1', 'tag', 'tag-1')
    assert [r.id for r in await store.for_scope(scope=SCOPE, limit=10)] == [session.id]
    assert [r.id for r in await store.for_scope(scope=OTHER, limit=10)] == [other.id]
