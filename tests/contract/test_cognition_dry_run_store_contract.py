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
    NewCognitionPace,
    NewCognitionRemindMirror,
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
                # `cognition_self_wake`/`cognition_remind_mirror` FK
                # `cognition_session.id` (step 3a-ii) — a table with a live FK
                # to one being truncated must be in the SAME statement, or
                # Postgres refuses the truncate outright, regardless of
                # whether it holds any rows.
                "truncate cognition_would_write, cognition_session_close, "
                "cognition_self_wake, cognition_remind_mirror, "
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


# --------------------------------------------------------------------------
# pace verbs and the mirrored `remind` family (step 3a-ii)
# --------------------------------------------------------------------------

async def test_record_pace_wake_at_returns_a_fresh_id(store):
    session_id = await store.record_session(session=_session(), recorded_at_ms=NOW)
    pace_id = await store.record_pace(
        pace=NewCognitionPace(
            session_id=session_id, unit="unit-1", verb="wake_at", wake_ref="w1",
            transition="created", subject_rows=("event:1",), why="check the venue",
            at_ms=NOW + 60_000, outcome="recorded", stimulus_id=42,
        ),
        recorded_at_ms=NOW,
    )
    assert pace_id is not None


async def test_record_pace_refused_carries_no_wake_ref(store):
    pace_id = await store.record_pace(
        pace=NewCognitionPace(
            session_id=None, unit="unit-1", verb="wake_at", wake_ref=None,
            transition="created", subject_rows=("event:1",), why="too far out",
            at_ms=None, outcome="refused", refusal_reason="horizon",
        ),
        recorded_at_ms=NOW,
    )
    assert pace_id is not None


async def test_record_pace_move_and_cancel_name_an_existing_ref(store):
    session_id = await store.record_session(session=_session(), recorded_at_ms=NOW)
    await store.record_pace(
        pace=NewCognitionPace(
            session_id=session_id, unit="unit-1", verb="wake_at", wake_ref="w2",
            transition="created", subject_rows=("event:2",), why="first look",
            at_ms=NOW + 60_000, outcome="recorded", stimulus_id=7,
        ),
        recorded_at_ms=NOW,
    )
    moved = await store.record_pace(
        pace=NewCognitionPace(
            session_id=session_id, unit="unit-1", verb="wake_move", wake_ref="w2",
            transition="moved", subject_rows=("event:2",), why="pushed back an hour",
            at_ms=NOW + 3_600_000, outcome="recorded", stimulus_id=8,
        ),
        recorded_at_ms=NOW,
    )
    cancelled = await store.record_pace(
        pace=NewCognitionPace(
            session_id=session_id, unit="unit-1", verb="wake_cancel", wake_ref="w2",
            transition="cancelled", subject_rows=("event:2",), why="no longer needed",
            at_ms=None, outcome="recorded",
        ),
        recorded_at_ms=NOW,
    )
    assert moved is not None
    assert cancelled is not None


async def test_record_remind_mirror_returns_a_fresh_id(store):
    session_id = await store.record_session(session=_session(), recorded_at_ms=NOW)
    mirror_id = await store.record_remind_mirror(
        mirror=NewCognitionRemindMirror(
            session_id=session_id, unit="unit-1", verb="remind", reminder_ref="r1",
            platform="signal", conversation_id="room-a", intent="tell",
            subject_rows=("note:1",), at_ms=NOW + 60_000, why=None, outcome="recorded",
        ),
        recorded_at_ms=NOW,
    )
    assert mirror_id is not None


async def test_record_remind_mirror_refused(store):
    mirror_id = await store.record_remind_mirror(
        mirror=NewCognitionRemindMirror(
            session_id=None, unit="unit-1", verb="remind", reminder_ref=None,
            platform="signal", conversation_id="room-a", intent="tell",
            subject_rows=(), at_ms=None, why=None, outcome="refused",
            refusal_reason="no_citations",
        ),
        recorded_at_ms=NOW,
    )
    assert mirror_id is not None


async def _remind(store, *, ref, room="room-a", platform="signal", unit="unit-1",
                  verb="remind", outcome="recorded", at=NOW):
    await store.record_remind_mirror(
        mirror=NewCognitionRemindMirror(
            session_id=None, unit=unit, verb=verb, reminder_ref=ref, platform=platform,
            conversation_id=room, intent="tell" if verb == "remind" else None,
            subject_rows=("note:1",) if verb == "remind" else (), at_ms=NOW + 60_000,
            why=None, outcome=outcome,
            refusal_reason="no_citations" if outcome == "refused" else None,
        ),
        recorded_at_ms=at,
    )


async def test_count_reminds_created_counts_a_rooms_recorded_reminds_since_an_instant(store):
    """The per-room daily act cap's count (`remind`, 30 §3): one room of one unit,
    recorded and not refused, at or after the instant; a move, a cancel, another
    room, another unit and an older one are not it."""
    await _remind(store, ref="a", at=NOW)
    await _remind(store, ref="b", at=NOW + 1_000)
    await _remind(store, ref="c", at=NOW - 10_000)                      # before the instant
    await _remind(store, ref="d", room="room-b")                        # another room
    await _remind(store, ref="e", platform="discord")                   # another platform
    await _remind(store, ref="f", unit="unit-2")                        # another unit
    await _remind(store, ref=None, outcome="refused")                   # refused: no act
    await _remind(store, ref="a", verb="remind_move")                   # a move is no act
    await _remind(store, ref="b", verb="remind_cancel")                 # nor a cancel
    count = await store.count_reminds_created(
        unit="unit-1", platform="signal", conversation_id="room-a", since_ms=NOW - 1,
    )
    assert count == 2
    assert await store.count_reminds_created(
        unit="unit-1", platform="signal", conversation_id="room-a", since_ms=NOW + 1_001,
    ) == 0


# ---- m1 (DEC-245): a jsonb column holds an object, not a string OF one -------


async def test_the_session_and_would_write_columns_are_jsonb_objects_not_strings(store):
    """SQL only: `json.dumps` handed to a `JSONB` column stored a JSON STRING that
    contained the JSON, so `situation->'grounding'` was NULL in every query and
    each reader had to decode twice."""
    if not isinstance(store, SqlCognitionDryRunStore):
        pytest.skip("a column type is a property of the SQL store")
    session_id = await store.record_session(
        session=_session(situation={"unit": "u", "grounding": {"records": [1]}},
                         agenda={"items": [{"kind": "message"}]}),
        recorded_at_ms=NOW,
    )
    await store.record_would_write(
        write=NewCognitionWouldWrite(
            session_id=session_id, unit="unit-1", tier="note", verb="note_create",
            platform="signal", conversation_id="room-a", args={"title": "t"},
            outcome="would_commit", candidate={"body": "b"},
        ),
        recorded_at_ms=NOW,
    )
    async with store._engine.connect() as conn:
        row = (await conn.execute(sa.text(
            "SELECT jsonb_typeof(situation) AS s, jsonb_typeof(agenda) AS a, "
            "jsonb_typeof(skill_versions) AS k, situation->'grounding'->'records'->>0 AS g "
            "FROM cognition_session WHERE id = :i"
        ), {"i": session_id})).one()
        write = (await conn.execute(sa.text(
            "SELECT jsonb_typeof(args) AS a, jsonb_typeof(candidate) AS c, args->>'title' AS ttl "
            "FROM cognition_would_write WHERE session_id = :i"
        ), {"i": session_id})).one()
    assert (row.s, row.a, row.k, row.g) == ("object", "object", "object", "1")
    assert (write.a, write.c, write.ttl) == ("object", "object", "t")
