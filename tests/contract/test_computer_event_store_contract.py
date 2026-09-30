"""One suite, both implementations of `ComputerEventStore` (design/
sandbox-runtime §12). Mirrors `tests/contract/test_tool_call_log_store_contract.py`:
the real-Postgres `pg_url` fixture, migrated up before the SQL store is
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
        "id": uuid4(), "created_at": now, "started_at": now, "finished_at": now,
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
                "INSERT INTO computer_events (id, started_at, finished_at, actor, "
                "platform, conversation_id, conversation_type, tag_id, scope, tool, "
                "path, trace_id, span_id, status) VALUES (gen_random_uuid(), now(), "
                "now(), 'someone', 's', 'c', 'group', '', 'x', 'computer_run', "
                "'direct', '', '', 'ok')"
            ))
