"""One suite, both implementations of `ComputerEventStore`.
Mirrors `tests/contract/test_tool_call_log_store_contract.py`: the
real-Postgres `pg_url` fixture, migrated up before the SQL store is
constructed, parametrized alongside the in-memory fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from nora.domain.computer_events import (
    ComputerEvent,
    ComputerEventStore,
    NaiveComputerEventDatetimeError,
    normalise_computer_event,
)
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeComputerEventStore

EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def _event(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(), "operation_id": uuid4(), "phase": "finished",
        "created_at": now, "started_at": now, "finished_at": now,
        "actor": "tag", "requester_person_id": "miles", "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group", "tag_id": "tag-1",
        "scope": "ab" * 16, "tool": "computer_run", "path": "direct",
        "trace_id": "0" * 31 + "1", "span_id": "0" * 15 + "2", "status": "ok",
        "request": {"command": "python3 -c 'print(1)'", "timeout_s": 120},
        "result": {"status": "ok", "exit_code": 0, "truncated": False},
        "output_ref": {"stdout": {"key": "k" * 64, "sha256": "k" * 64, "bytes": 2}},
    }
    defaults.update(overrides)
    return ComputerEvent(**defaults)


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeComputerEventStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield SqlComputerEventStore(engine)
    await engine.dispose()


def test_both_satisfy_the_port():
    assert isinstance(FakeComputerEventStore(), ComputerEventStore)
    assert isinstance(SqlComputerEventStore(engine=None), ComputerEventStore)  # type: ignore[arg-type]


async def test_an_appended_event_round_trips_every_field(store):
    event = _event()
    await store.append(event)
    [got] = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert got == normalise_computer_event(event)


async def test_a_nullable_requester_stays_null(store):
    await store.append(_event(actor="dreamer:proactive", requester_person_id=None))
    [got] = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert (got.actor, got.requester_person_id) == ("dreamer:proactive", None)


async def test_recent_is_scoped_to_one_conversation(store):
    await store.append(_event(conversation_id="conv-1"))
    await store.append(_event(conversation_id="conv-2"))
    rows = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert {r.conversation_id for r in rows} == {"conv-1"}


async def test_recent_excludes_anything_before_since(store):
    old = _event(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    new = _event(created_at=datetime(2026, 9, 30, tzinfo=UTC))
    await store.append(old)
    await store.append(new)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2026, 6, 1, tzinfo=UTC), limit=10,
    )
    assert [r.id for r in rows] == [new.id]


async def test_recent_orders_newest_first_and_respects_limit(store):
    events = [_event(created_at=datetime(2026, 9, d, tzinfo=UTC)) for d in (10, 12, 14)]
    for e in events:
        await store.append(e)
    rows = await store.recent(conversation_id="conv-1", since=EPOCH, limit=2)
    assert [r.id for r in rows] == [events[2].id, events[1].id]


async def test_append_refuses_a_naive_created_at(store):
    with pytest.raises(NaiveComputerEventDatetimeError):
        await store.append(_event(created_at=datetime(2026, 9, 30, 12, 0, 0)))


def test_an_unknown_actor_or_path_is_refused_at_construction():
    with pytest.raises(ValueError, match="actor"):
        _event(actor="someone")
    with pytest.raises(ValueError, match="actor"):
        _event(actor="dreamer:")
    with pytest.raises(ValueError, match="path"):
        _event(path="sideways")


# -- append-only, enforced by the database (SQL only) -------------------------


@pytest.fixture
async def sql_engine(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize("statement", [
    "UPDATE computer_events SET status = 'unavailable'",
    "UPDATE computer_events SET request = '{}'::jsonb",
    "DELETE FROM computer_events",
    "TRUNCATE computer_events",
])
async def test_the_table_refuses_every_rewrite(sql_engine, statement):
    """The trail cannot be edited after the fact, by any path: an UPDATE, a
    DELETE and a TRUNCATE are refused by migration 0080's trigger, and the
    row is still there, unchanged, afterwards."""
    store = SqlComputerEventStore(sql_engine)
    event = _event()
    await store.append(event)
    with pytest.raises(DBAPIError, match="append-only"):
        async with sql_engine.begin() as conn:
            await conn.execute(sa.text(statement))
    [got] = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert got == normalise_computer_event(event)


async def test_the_database_refuses_an_unknown_actor(sql_engine):
    """The CHECK is a second wall behind the domain's own refusal."""
    with pytest.raises(IntegrityError):
        async with sql_engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO computer_events (id, operation_id, phase, started_at, "
                "finished_at, actor, platform, conversation_id, conversation_type, "
                "tag_id, scope, tool, path, trace_id, span_id, status) VALUES "
                "(gen_random_uuid(), gen_random_uuid(), 'finished', now(), now(), "
                "'someone', 's', 'c', 'group', '', 'x', 'computer_run', 'direct', "
                "'', '', 'ok')"
            ))


def test_a_started_row_has_no_finished_time_and_a_finished_row_has_one():
    with pytest.raises(ValueError, match="finished_at"):
        _event(phase="started")
    with pytest.raises(ValueError, match="finished_at"):
        _event(phase="finished", finished_at=None)
    with pytest.raises(ValueError, match="phase"):
        _event(phase="halfway")


async def test_orphaned_starts_are_starts_with_no_finished_row(store):
    """The crash marker: a started row whose operation never finished.
    Oldest first; a start newer than `older_than` (maybe still running) and a
    start that finished are both left out."""
    t = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    done, crashed, running = uuid4(), uuid4(), uuid4()
    for op, at in ((done, t), (crashed, t.replace(minute=5)), (running, t.replace(hour=13))):
        await store.append(_event(operation_id=op, phase="started", status="started",
                                  finished_at=None, created_at=at, started_at=at, result={}))
    await store.append(_event(operation_id=done, created_at=t.replace(minute=1),
                              started_at=t, finished_at=t.replace(minute=1)))
    orphans = await store.orphaned_starts(older_than=t.replace(hour=12, minute=30), limit=10)
    assert [o.operation_id for o in orphans] == [crashed]


async def test_one_operation_has_at_most_one_row_per_phase(store):
    op = uuid4()
    await store.append(_event(operation_id=op))
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError (SQL), RuntimeError (fake)
        await store.append(_event(operation_id=op))


async def test_a_sessions_rows_come_back_newest_first_and_only_its_own(store):
    """`session_id` (migration `0082`) round-trips, and `for_session` reads
    one session's rows, what its progress is derived from."""
    session, other = uuid4(), uuid4()
    t = datetime.now(UTC)
    from datetime import timedelta
    older = _event(session_id=session, created_at=t, started_at=t, finished_at=t)
    newer = _event(session_id=session, created_at=t + timedelta(seconds=1),
                   started_at=t + timedelta(seconds=1), finished_at=t + timedelta(seconds=1))
    await store.append(older)
    await store.append(newer)
    await store.append(_event(session_id=other))
    await store.append(_event())  # no session: a row from before 0082
    rows = await store.for_session(session_id=session, limit=10)
    assert [r.id for r in rows] == [newer.id, older.id]
    assert {r.session_id for r in rows} == {session}


async def test_the_sweeper_is_an_actor(store):
    await store.append(_event(actor="sweeper", tool="computer_session",
                              requester_person_id=None))
    [got] = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert got.actor == "sweeper"


async def test_recent_can_be_narrowed_to_one_platform(store):
    """Two networks can carry the same conversation id."""
    await store.append(_event(platform="signal"))
    await store.append(_event(platform="discord"))
    rows = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10,
                              platform="discord")
    assert [r.platform for r in rows] == ["discord"]
    assert len(await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)) == 2
