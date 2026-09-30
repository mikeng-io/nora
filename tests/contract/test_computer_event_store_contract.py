"""One suite, both implementations of `ComputerEventStore` (design/
sandbox-runtime §12). Mirrors `tests/contract/test_tool_call_log_store_contract.py`:
the real-Postgres `pg_url` fixture, migrated up before the SQL store is
constructed, parametrized alongside the in-memory fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.computer_events import (
    ComputerEvent,
    ComputerEventStore,
    NaiveComputerEventDatetimeError,
)
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeComputerEventStore

EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


def _event(**overrides):
    defaults = {
        "id": uuid4(), "created_at": datetime.now(UTC), "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group", "tag_id": "tag-1",
        "scope": "ab" * 16, "tool": "computer_run", "status": "ok",
        "exit_code": 0, "duration_ms": 12,
        "detail": {"command": "python3 -c 'print(1)'", "output_chars": 2,
                   "stdout_head": "1\n", "truncated": False},
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
    rows = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert len(rows) == 1
    got = rows[0]
    assert (got.id, got.platform, got.conversation_type, got.tag_id, got.scope) == (
        event.id, "signal", "group", "tag-1", "ab" * 16,
    )
    assert (got.tool, got.status, got.exit_code, got.duration_ms) == ("computer_run", "ok", 0, 12)
    assert got.detail == event.detail
    assert got.created_at.tzinfo is not None


async def test_nullable_run_fields_stay_null(store):
    await store.append(_event(tool="computer_read", exit_code=None, duration_ms=None,
                              detail={"path": "a.txt"}))
    [got] = await store.recent(conversation_id="conv-1", since=EPOCH, limit=10)
    assert got.exit_code is None
    assert got.duration_ms is None
    assert got.detail == {"path": "a.txt"}


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
