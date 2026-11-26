"""SQL note create and revision receipts commit inside their own transactions."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import COMPREHEND_RESULT, NOTE, NOTE_REVISION

NOW = 1_760_000_000_000


async def _count(conn, table, *where) -> int:
    stmt = sa.select(sa.func.count()).select_from(table)
    if where:
        stmt = stmt.where(*where)
    return int((await conn.execute(stmt)).scalar_one())


async def _receipt(conn, payload, *, work_id: str) -> None:
    await conn.execute(sa.insert(COMPREHEND_RESULT).values(
        work_id=work_id, input_hash="generated-input", session_id=1,
        part="act_result", payload={"status": "ok", "id": payload.id},
    ))


async def test_create_receipt_failure_rolls_the_note_back_and_a_retry_commits(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        notes = SqlNoteStore(engine)
        kwargs = {
            "unit": "u", "title": "generated", "description": "d", "uncertainty": "q",
            "closing_condition": "c", "established_in": "signal:room-a",
            "body": "generated body", "established_by": "nora", "now_ms": NOW,
            "anchor_date": 20251101,
        }

        async def crash(conn, note):
            await _receipt(conn, note, work_id="generated-note-create")
            raise RuntimeError("generated crash at the receipt boundary")

        with pytest.raises(RuntimeError, match="generated crash"):
            await notes.create(**kwargs, receipt=crash)
        async with engine.connect() as conn:
            assert await _count(conn, NOTE) == 0
            assert await _count(conn, NOTE_REVISION) == 0
            assert await _count(conn, COMPREHEND_RESULT) == 0

        note = await notes.create(
            **kwargs,
            receipt=lambda conn, row: _receipt(conn, row, work_id="generated-note-create"),
        )
        async with engine.connect() as conn:
            assert await _count(conn, NOTE, NOTE.c.id == note.id) == 1
            assert await _count(conn, NOTE_REVISION, NOTE_REVISION.c.note_id == note.id) == 1
            assert await _count(
                conn, COMPREHEND_RESULT,
                COMPREHEND_RESULT.c.work_id == "generated-note-create",
            ) == 1
    finally:
        await engine.dispose()


async def test_revision_receipt_failure_leaves_the_head_and_a_retry_commits(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        notes = SqlNoteStore(engine)
        note = await notes.create(
            unit="u", title="generated", description="d", uncertainty="q",
            closing_condition="c", established_in="signal:room-a",
            body="generated body", established_by="nora", now_ms=NOW,
            anchor_date=20251101,
        )

        async def crash(conn, revision):
            await _receipt(conn, revision, work_id="generated-note-revision")
            raise RuntimeError("generated crash at the receipt boundary")

        with pytest.raises(RuntimeError, match="generated crash"):
            await notes.append_revision(
                unit="u", note_id=note.id, body="generated revision",
                parent_revision_id=note.head_revision_id, established_by="nora",
                now_ms=NOW, receipt=crash,
            )
        async with engine.connect() as conn:
            assert await _count(
                conn, NOTE_REVISION, NOTE_REVISION.c.body == "generated revision",
            ) == 0
            assert (await conn.execute(
                sa.select(NOTE.c.head_revision_id).where(NOTE.c.id == note.id)
            )).scalar_one() == note.head_revision_id
            assert await _count(
                conn, COMPREHEND_RESULT,
                COMPREHEND_RESULT.c.work_id == "generated-note-revision",
            ) == 0

        revision = await notes.append_revision(
            unit="u", note_id=note.id, body="generated revision",
            parent_revision_id=note.head_revision_id, established_by="nora",
            now_ms=NOW,
            receipt=lambda conn, row: _receipt(conn, row, work_id="generated-note-revision"),
        )
        async with engine.connect() as conn:
            assert await _count(
                conn, NOTE_REVISION, NOTE_REVISION.c.body == "generated revision",
            ) == 1
            assert await _count(
                conn, COMPREHEND_RESULT,
                COMPREHEND_RESULT.c.work_id == "generated-note-revision",
            ) == 1
            assert (await conn.execute(
                sa.select(NOTE.c.head_revision_id).where(NOTE.c.id == note.id)
            )).scalar_one() == revision.id
    finally:
        await engine.dispose()
