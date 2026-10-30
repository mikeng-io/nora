"""The tag-trace store contract, held against BOTH implementations: the SQL
store and the in-memory fake (`port → two implementations`).

What is pinned: `save`/`get`/`recent`/`recent_unit` scope, order and
completeness — `recent` is one room, `recent_unit` is the whole unit
newest-first, `get` is unit-filtered by `turn_id`."""

import uuid

import pytest

from nora.domain.tag_trace import TagTrace
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.testing.tag_traces import FakeTagTraceStore


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlTagTraceStore(create_sql_engine(pg_url))
        yield s
        await s._engine.dispose()
    else:
        yield FakeTagTraceStore()


def _trace(tag_id=None, cid="room-a", unit="u1", turn_id=None, created=1000,
           occasion="tag") -> TagTrace:
    return TagTrace(
        tag_id=tag_id or uuid.uuid4().hex, conversation_id=cid,
        messages=({"role": "user", "content": [{"text": "hi"}]},),
        transcript=(), outcome="send_reply", created_at=created,
        unit=unit, occasion=occasion, turn_id=turn_id,
    )


async def test_saved_traces_come_back_by_room_and_unit(store):
    a = _trace(cid="room-a", created=100)
    b = _trace(cid="room-b", created=200)
    other_unit = _trace(cid="room-a", unit="other", created=300)
    for t in (a, b, other_unit):
        await store.save(t)

    room = await store.recent(conversation_id="room-a", limit=10)
    assert {t.tag_id for t in room} == {a.tag_id, other_unit.tag_id}

    unit = await store.recent_unit(unit="u1", limit=10)
    assert [t.tag_id for t in unit] == [b.tag_id, a.tag_id]


async def test_recent_unit_is_newest_first_and_bounded(store):
    for i in range(5):
        await store.save(_trace(cid="room-a", created=1000 + i))
    unit = await store.recent_unit(unit="u1", limit=3)
    assert len(unit) == 3
    createds = [t.created_at for t in unit]
    assert createds == sorted(createds, reverse=True)


async def test_get_returns_the_turn_only_within_its_unit(store):
    saved = _trace(unit="u1")
    await store.save(saved)
    stored = (await store.recent_unit(unit="u1", limit=1))[0]
    got = await store.get(turn_id=stored.turn_id, unit="u1")
    assert got is not None and got.tag_id == saved.tag_id
    assert await store.get(turn_id=stored.turn_id, unit="other") is None


async def test_a_zero_limit_reads_nothing(store):
    await store.save(_trace())
    assert await store.recent_unit(unit="u1", limit=0) == ()
    assert await store.recent(conversation_id="room-a", limit=0) == ()
