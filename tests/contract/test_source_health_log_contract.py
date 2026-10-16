"""Shared contract for the source health log.

`nora.infra.sql.source_health.SqlSourceHealthLog` over real Postgres and
`nora.testing.stimuli.InMemorySourceHealthLog`. The property: a row is
appended with what it was told, and a write that fails answers False rather
than raising (health is recorded beside the work it describes and must never
stop it).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.testing.stimuli import InMemorySourceHealthLog

NOW = 1_790_000_000_000


@pytest.fixture(params=["memory", "sql"])
async def log_and_rows(request: Any) -> AsyncIterator[tuple[Any, Any]]:
    if request.param == "memory":
        log = InMemorySourceHealthLog()

        async def rows() -> list[tuple[str, str, str, str, int, str]]:
            return [(r.source, r.scope, r.event, r.outcome, r.at_ms, r.detail)
                    for r in log.rows]

        yield log, rows
        return
    engine = create_sql_engine(request.getfixturevalue("pg_url"))

    async def sql_rows() -> list[tuple[str, str, str, str, int, str]]:
        async with engine.connect() as conn:
            res = await conn.execute(sa.text(
                "select source, scope, event, outcome, at_ms, detail "
                "from source_health order by id"))
            return [tuple(r) for r in res.all()]  # type: ignore[misc]

    try:
        yield SqlSourceHealthLog(engine, clock=lambda: NOW / 1000), sql_rows
    finally:
        await engine.dispose()


async def test_a_poll_and_a_transition_are_recorded_in_order(log_and_rows):
    log, rows = log_and_rows
    assert await log.record(source="presence", scope="c1", event="poll",
                            outcome="ok", at_ms=NOW)
    assert await log.record(source="signal", scope="", event="transition",
                            outcome="down", at_ms=NOW + 1, detail="socket closed")
    assert await rows() == [
        ("presence", "c1", "poll", "ok", NOW, ""),
        ("signal", "", "transition", "down", NOW + 1, "socket closed"),
    ]


async def test_transitions_read_back_one_source_from_a_time_oldest_first(log_and_rows):
    log, _ = log_and_rows
    await log.record(source="social.instagram", scope="", event="transition",
                     outcome="up", at_ms=NOW)
    await log.record(source="social.instagram", scope="", event="poll",
                     outcome="ok", at_ms=NOW + 1)
    await log.record(source="signal", scope="", event="transition",
                     outcome="down", at_ms=NOW + 2)
    await log.record(source="social.instagram", scope="story", event="transition",
                     outcome="stale_query", at_ms=NOW + 3, detail="stale_query")
    got = await log.transitions(source="social.instagram", since_ms=NOW + 1)
    assert [(r.scope, r.outcome, r.at_ms, r.detail) for r in got] == [
        ("story", "stale_query", NOW + 3, "stale_query"),
    ]
    assert len(await log.transitions(source="social.instagram", since_ms=NOW)) == 2


async def test_a_failed_write_answers_false_and_never_raises(pg_url):
    """The SQL half on a dead engine; the fake's `fail` switch mirrors it."""
    engine = create_sql_engine(pg_url.replace("/t_", "/missing_t_"))
    try:
        ok = await SqlSourceHealthLog(engine).record(
            source="presence", scope="c1", event="poll", outcome="ok", at_ms=NOW)
    finally:
        await engine.dispose()
    assert ok is False
    fake = InMemorySourceHealthLog()
    fake.fail = True
    assert await fake.record(source="presence", scope="c1", event="poll",
                             outcome="ok", at_ms=NOW) is False
    assert fake.rows == []
