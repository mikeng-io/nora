"""One suite, both implementations of `ToolCallLogStore`. Mirrors
`tests/contract/test_compute_job_store_contract.py`'s own shape — the same
real-Postgres `pg_url` fixture, migrated up before the SQL store is
constructed, parametrized alongside the in-memory fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.tool_call_log import NaiveToolCallLogDatetimeError, ToolCallLogEntry
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.testing import FakeToolCallLogStore


def _entry(**overrides):
    defaults = {
        "id": uuid4(), "conversation_id": "conv-1", "conversation_type": "group",
        "tag_id": "tag-1", "tool_name": "place_info",
        "tool_input": {"name": "Yoho Mall"},
        "tool_result": {"status": "ok", "rating": 4.2},
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ToolCallLogEntry(**defaults)


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeToolCallLogStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield SqlToolCallLogStore(engine)
    await engine.dispose()


async def test_an_appended_entry_round_trips(store):
    entry = _entry()
    await store.append(entry)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert len(rows) == 1
    assert rows[0].tool_result == {"status": "ok", "rating": 4.2}


async def test_recent_is_scoped_to_one_conversation(store):
    await store.append(_entry(conversation_id="conv-1"))
    await store.append(_entry(conversation_id="conv-2"))
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert {r.conversation_id for r in rows} == {"conv-1"}


async def test_recent_excludes_anything_before_since(store):
    old = _entry(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    new = _entry(created_at=datetime(2026, 8, 14, tzinfo=UTC))
    await store.append(old)
    await store.append(new)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2026, 6, 1, tzinfo=UTC), limit=10,
    )
    assert len(rows) == 1
    assert rows[0].id == new.id


async def test_recent_orders_newest_first(store):
    first = _entry(created_at=datetime(2026, 8, 10, tzinfo=UTC))
    second = _entry(created_at=datetime(2026, 8, 12, tzinfo=UTC))
    third = _entry(created_at=datetime(2026, 8, 14, tzinfo=UTC))
    for e in (first, second, third):
        await store.append(e)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert [r.id for r in rows] == [third.id, second.id, first.id]


async def test_recent_respects_limit(store):
    for _ in range(5):
        await store.append(_entry())
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=2,
    )
    assert len(rows) == 2


async def test_append_refuses_a_naive_created_at(store):
    with pytest.raises(NaiveToolCallLogDatetimeError):
        await store.append(_entry(created_at=datetime(2026, 8, 14, 12, 0, 0)))
