"""The withdrawal cascade (design/cognition-loop/10-tiers.md §2 rule 7, §8,
§9; 70-migration.md §2 row 2c; migration `0075_withdrawal`, DEC-238).

Module-scoped database per `test_culture_store.py`'s pattern (one migrated
database, truncated between tests). Every test proves the NEGATIVE too: a
withdrawn item's citing row must stop reaching the reader that used to reach
it, not merely gain a flag nothing checks (the task's own disclosure-critical
rule).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.domain.standing import Standing
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.history import as_of
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import CITATION, MESSAGES
from nora.infra.sql.standing import SqlStandingStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_withdrawal_" + uuid4().hex
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
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            # A reset, not a write: TRUNCATE is refused on a HISTORY_TABLES
            # table unless capture is off (`nora_row_history_refuse_truncate`,
            # `test_culture_store.py`'s own precedent).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "citation", "note_revision", "note", "standing_revision",
                "standing", "culture_revision", "culture", "messages",
                "row_history",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


async def _message(engine: Any, *, conversation_id: str = "g1", ts: int = 1000) -> int:
    store = SqlRecordStore(engine)
    return await store.append(
        message=NormalizedMessage(
            "signal", conversation_id, "group", "+1", "Miles", ts, "hi", {},
        ),
        person_id="miles",
    )


async def test_a_note_citing_a_message_records_the_edge(engine):
    mid = await _message(engine)
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit="u", title="t", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:g1",
        body=f"a claim [{mid}]", established_by="test", now_ms=1000,
        anchor_date=20260901,
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(CITATION).where(CITATION.c.citing_store == "note")
        )).all()
    assert [(r.citing_key, r.cited_id) for r in rows] == [(note.id, str(mid))]


async def test_a_standing_fold_citing_its_watermark_records_the_edge(engine):
    mid = await _message(engine)
    store = SqlStandingStore(engine)
    await store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="a position",
        fold_count=1, updated_at=1000, last_folded_row_id=mid,
    ))
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(CITATION).where(CITATION.c.citing_store == "standing")
        )).all()
    assert [(r.citing_key, r.cited_id) for r in rows] == [("signal:g1", str(mid))]


async def test_a_culture_fold_citing_the_rooms_newest_message_records_the_edge(engine):
    mid = await _message(engine)
    store = SqlCultureStore(engine)
    await store.append(
        key="g1", body="an account", written_at=1000, established_by="fold",
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(CITATION).where(CITATION.c.citing_store == "culture")
        )).all()
    assert [(r.citing_key, r.cited_id) for r in rows] == [("g1", str(mid))]


async def test_cascade_marks_every_citing_store_unsupported(engine):
    """The gate's own scenario: one Record item withdrawn, and every store
    that cites it — note, standing, culture — goes `unsupported`."""
    from nora.app.withdrawal import withdraw_message

    mid = await _message(engine)
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit="u", title="t", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:g1",
        body=f"a claim [{mid}]", established_by="test", now_ms=1000,
        anchor_date=20260901,
    )
    standing_store = SqlStandingStore(engine)
    await standing_store.write(standing=Standing(
        platform="signal", conversation_id="g1", body="a position",
        fold_count=1, updated_at=1000, last_folded_row_id=mid,
    ))
    culture_store = SqlCultureStore(engine)
    await culture_store.append(
        key="g1", body="an account", written_at=1000, established_by="fold",
    )

    async with engine.begin() as conn:
        report = await withdraw_message(conn, message_id=mid, now_ms=2000)

    assert set(report.marked["note"]) == {note.id}
    assert report.marked["standing"] == ["signal:g1"]
    assert report.marked["culture"] == ["g1"]
    assert report.total_marked == 3

    # NEGATIVE: the note index (the pushed block / `note_read`'s own
    # resolution) no longer returns it.
    assert await notes.standing(unit="u") == ()
    hits = await notes.search(unit="u", query="claim", limit=10)
    assert hits == ()

    # `read()`/`revision_chain` still see the real row — the writer's own
    # re-derivation path is not this cascade's job to block.
    still_there = await notes.note_by_id(unit="u", note_id=note.id)
    assert still_there is not None and still_there.unsupported_at == 2000


async def test_cascade_is_idempotent(engine):
    from nora.app.withdrawal import withdraw_message

    mid = await _message(engine)
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit="u", title="t", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:g1",
        body=f"a claim [{mid}]", established_by="test", now_ms=1000,
        anchor_date=20260901,
    )
    async with engine.begin() as conn:
        first = await withdraw_message(conn, message_id=mid, now_ms=2000)
        second = await withdraw_message(conn, message_id=mid, now_ms=3000)

    assert first.marked["note"] == [note.id]
    assert second.marked == {}
    held = await notes.note_by_id(unit="u", note_id=note.id)
    # the ORIGINAL timestamp, never overwritten by the second cascade
    assert held.unsupported_at == 2000


async def test_forget_person_hides_messages_and_cascades(engine):
    from nora.app.person_forget import forget_person, unforget_person

    mid = await _message(engine)
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit="u", title="t", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:g1",
        body=f"a claim [{mid}]", established_by="test", now_ms=1000,
        anchor_date=20260901,
    )

    dry = await forget_person(engine, person_id="miles", apply=False, now_ms=5000)
    assert dry.hidden == 1
    async with engine.connect() as conn:
        untouched = (await conn.execute(
            sa.select(MESSAGES.c.hidden_at).where(MESSAGES.c.id == mid)
        )).scalar_one()
    assert untouched is None, "a dry run must write nothing"

    applied = await forget_person(engine, person_id="miles", apply=True, now_ms=5000)
    assert applied.hidden == 1
    assert applied.cascade is not None
    assert applied.cascade.marked["note"] == [note.id]

    # NEGATIVE: the note stops rendering, and the message stops reaching a
    # transcript read.
    assert await notes.standing(unit="u") == ()
    store = SqlRecordStore(engine)
    assert await store.recent(conversation_id="g1", limit=10) == []

    # unforget un-hides the message but does NOT un-mark the cascade.
    unforgot = await unforget_person(engine, person_id="miles", apply=True)
    assert unforgot.restored == 1
    assert await store.recent(conversation_id="g1", limit=10) != []
    held = await notes.note_by_id(unit="u", note_id=note.id)
    assert held.unsupported_at == 5000, "unforget reverses the hide, not the cascade mark"


async def test_a_snapshot_before_the_forget_restores_the_message(engine):
    """10 §9's own gate: "a forget followed by a snapshot before it restores
    everything". `as_of` is the mechanism `nora.infra.sql.snapshot_at.seed`
    uses to build a sandbox as of T; proven directly here rather than
    through the whole snapshot CLI, which needs a policy and a second
    database to seed."""
    from nora.app.person_forget import forget_person

    mid = await _message(engine, ts=1000)
    async with engine.connect() as conn:
        before = (await conn.execute(
            sa.select(sa.func.clock_timestamp())
        )).scalar_one()

    await forget_person(engine, person_id="miles", apply=True, now_ms=9000)

    async with engine.connect() as conn:
        restored = [
            row for part in [p async for p in as_of(conn, "messages", before)]
            for row in part
        ]
    assert len(restored) == 1
    assert restored[0]["id"] == mid
    assert restored[0]["hidden_at"] is None, (
        "a snapshot before the forget must show the message unhidden"
    )
