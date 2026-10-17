"""Migration `0116_note_citations` backfills what every stored note revision
cites, and queues every note for the drain to re-project with its `cites`
edges, so a note written before the column is reached like one written after."""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from alembic import command

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config, sync_url_for
from nora.infra.sql.notes import SqlNoteStore


async def _two_notes(url: str) -> tuple[str, str, str]:
    engine = create_sql_engine(url)
    try:
        store = SqlNoteStore(engine)
        note = await store.create(
            unit="unit-a", title="Friday dinner", description="where to eat",
            uncertainty="the place", closing_condition="someone books",
            established_in="g1", body="## Decided\n## Open\n- where [12, rollup(outing|x|a/b)]",
            established_by="tag:p1", now_ms=10, anchor_date=20261020,
        )
        revision = await store.append_revision(
            unit="unit-a", note_id=note.id, body="a legacy body [7] with [no key]",
            parent_revision_id=note.head_revision_id, established_by="tag:p1", now_ms=20,
        )
        bare = await store.create(
            unit="unit-a", title="Saturday hike", description="whether it runs",
            uncertainty="the weather", closing_condition="the forecast",
            established_in="g1", body="## Decided\n## Open\n",
            established_by="tag:p1", now_ms=30, anchor_date=20261021,
        )
    finally:
        await engine.dispose()
    return note.head_revision_id, revision.id, bare.id


def test_the_backfill_stores_each_revision_s_citations_and_queues_every_note(pg_url):
    first, second, bare = asyncio.run(_two_notes(pg_url))
    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "0115_watermark_bigint")
    engine = sa.create_engine(sync_url_for(pg_url))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM memory_projection_queue"))
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            cited = dict(conn.execute(sa.text(
                "SELECT id::text, citations FROM note_revision"
            )).all())
            queued = conn.execute(sa.text(
                "SELECT source_id FROM memory_projection_queue WHERE kind = 'note'"
            )).scalars().all()
            notes = conn.execute(sa.text("SELECT id::text FROM note")).scalars().all()
    finally:
        engine.dispose()
    assert cited[first] == ["12", "rollup(outing|x|a/b)"]
    assert cited[second] == ["7"]
    assert [c for r, c in cited.items() if r not in (first, second)] == [[]]
    assert sorted(queued) == sorted(notes) and bare in queued
