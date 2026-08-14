from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.tool_call_log import ToolCallLogEntry
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.tool_call_log import SqlToolCallLogStore


@pytest.mark.asyncio
async def test_a_logged_entry_round_trips(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    store = SqlToolCallLogStore(engine)
    entry = ToolCallLogEntry(
        id=uuid4(), conversation_id="conv-1", conversation_type="group",
        tag_id="tag-1", tool_name="place_info",
        tool_input={"name": "Yoho Mall"}, tool_result={"status": "ok", "rating": 4.2},
        created_at=datetime.now(UTC),
    )
    await store.append(entry)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert len(rows) == 1
    assert rows[0].tool_result == {"status": "ok", "rating": 4.2}
    await engine.dispose()
