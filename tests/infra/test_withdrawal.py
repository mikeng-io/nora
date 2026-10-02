"""The citation edges (migration `0075_withdrawal`) and the hide a platform delete
does (`SqlRecordStore.hide_message`).

Module-scoped database per `test_culture_store.py`'s pattern (one migrated
database, truncated between tests)."""

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
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.history import as_of
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import CITATION, MESSAGES, NOTE
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


def _docs(engine: Any) -> SqlDocumentStore:
    return SqlDocumentStore(engine)


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


async def test_hide_message_hides_a_row_once_and_a_snapshot_before_it_restores_the_row(engine):
    """A platform delete hides the message (`RecordStore.hide_message`): the first call
    hides it and says so, a second keeps the first timestamp and says False, and the
    transcript read no longer returns it. A snapshot taken before the hide shows it
    unhidden (`as_of` is what `nora.infra.sql.snapshot_at.seed` builds a sandbox from)."""
    store = SqlRecordStore(engine)
    mid = await _message(engine, ts=1000)
    async with engine.connect() as conn:
        before = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()

    assert await store.hide_message(message_id=mid, now_ms=9000) is True
    assert await store.hide_message(message_id=mid, now_ms=9500) is False
    assert await store.hide_message(message_id=987_654, now_ms=9000) is False

    async with engine.connect() as conn:
        held = (await conn.execute(
            sa.select(MESSAGES.c.hidden_at).where(MESSAGES.c.id == mid)
        )).scalar_one()
    assert held == 9000, "the second delete keeps the first hide's timestamp"
    assert await store.recent(conversation_id="g1", limit=10) == [], "a hidden row is not read"

    async with engine.connect() as conn:
        restored = [
            row for part in [p async for p in as_of(conn, "messages", before)]
            for row in part
        ]
    assert len(restored) == 1 and restored[0]["id"] == mid
    assert restored[0]["hidden_at"] is None


async def test_a_note_marked_unsupported_stops_reaching_the_index_and_search(engine):
    """No writer marks a row any more (the cascade is gone), but a row marked before it
    was removed still stays out of the pushed index and `note_search`; `note_by_id`, the
    writer's read, still returns it."""
    mid = await _message(engine)
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit="u", title="t", description="d", uncertainty="open",
        closing_condition="c", established_in="signal:g1",
        body=f"a claim [{mid}]", established_by="test", now_ms=1000,
        anchor_date=20260901,
    )
    assert len(await notes.standing(unit="u")) == 1
    async with engine.begin() as conn:
        await conn.execute(sa.update(NOTE).where(NOTE.c.id == note.id).values(unsupported_at=2000))
    assert await notes.standing(unit="u") == ()
    assert await notes.search(unit="u", query="claim", limit=10) == ()
    still_there = await notes.note_by_id(unit="u", note_id=note.id)
    assert still_there is not None and still_there.unsupported_at == 2000
