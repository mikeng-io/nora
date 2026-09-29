"""Shared contract for `CognitionDryRunStore` (design/cognition-loop/
70-migration.md step 3a). Every implementation runs every test.

Two implementations: `nora.infra.sql.cognition_dry_run.SqlCognitionDryRunStore`
over real Postgres, and `nora.testing.cognition_dry_run_store.
InMemoryCognitionDryRunStore`. All three tables are append-only, so the
contract is simple: every `record_*` call returns a fresh id and the row is
never touched again — there is no update path to test.

The database is built ONCE for this module and truncated between tests
(AGENTS.md, "a contract suite scopes its database to the MODULE").
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.ports import (
    NewCognitionSession,
    NewCognitionSessionClose,
    NewCognitionWouldWrite,
)
from nora.infra.sql.cognition_dry_run import SqlCognitionDryRunStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore

NOW = 1_790_000_000_000


@pytest.fixture(scope="module")
def _cognition_dry_run_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_cognition_dry_run_" + uuid4().hex
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
async def store(request: Any) -> AsyncIterator[Any]:
    if request.param == "memory":
        yield InMemoryCognitionDryRunStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_cognition_dry_run_db"))
    try:
        yield SqlCognitionDryRunStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "truncate cognition_would_write, cognition_session_close, "
                "cognition_session restart identity"
            ))
        await engine.dispose()


def _session(**overrides: object) -> NewCognitionSession:
    base: dict[str, object] = {
        "unit": "unit-1", "mode": "shadow", "started_at_ms": NOW,
        "from_stimulus_id": 0, "through_stimulus_id": 5,
        "situation": {"unit": "unit-1"}, "agenda": {"items": []},
        "doctrine_sha256": "abc123", "skill_versions": {"note": "v1"},
        "config_revision_id": None, "model_id": None, "model_route": None, "wake_id": None,
    }
    base.update(overrides)
    return NewCognitionSession(**base)  # type: ignore[arg-type]


async def test_record_session_returns_a_fresh_id(store):
    a = await store.record_session(session=_session(), recorded_at_ms=NOW)
    b = await store.record_session(session=_session(), recorded_at_ms=NOW)
    assert a != b


async def test_record_close_returns_a_fresh_id_against_its_session(store):
    session_id = await store.record_session(session=_session(), recorded_at_ms=NOW)
    close_id = await store.record_close(
        close=NewCognitionSessionClose(
            session_id=session_id, item_index=0, kind="message", reason="written",
        ),
        recorded_at_ms=NOW,
    )
    assert close_id is not None


async def test_record_would_write_would_commit(store):
    session_id = await store.record_session(session=_session(), recorded_at_ms=NOW)
    write_id = await store.record_would_write(
        write=NewCognitionWouldWrite(
            session_id=session_id, unit="unit-1", tier="standing",
            verb="standing_revise", platform="signal", conversation_id="room-a",
            args={"where_it_stands": "dinner is on"}, outcome="would_commit",
            candidate={"body": "dinner is on"},
        ),
        recorded_at_ms=NOW,
    )
    assert write_id is not None


async def test_record_would_write_refused(store):
    write_id = await store.record_would_write(
        write=NewCognitionWouldWrite(
            session_id=None, unit="unit-1", tier="note", verb="note_create",
            platform="signal", conversation_id="room-a", args={}, outcome="refused",
            refusal_reason="missing_section", refusal_detail="## Open is missing",
        ),
        recorded_at_ms=NOW,
    )
    assert write_id is not None


async def test_a_would_write_with_no_session_is_allowed(store):
    """A would-write recorded outside a session row (a direct wrapper test)
    still has somewhere to land — `session_id` is nullable."""
    write_id = await store.record_would_write(
        write=NewCognitionWouldWrite(
            session_id=None, unit="unit-1", tier="event", verb="event_propose",
            platform="signal", conversation_id="room-a", args={},
            outcome="would_commit", candidate={"title": "dinner"},
        ),
        recorded_at_ms=NOW,
    )
    assert write_id is not None
