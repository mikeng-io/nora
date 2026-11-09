"""The generated `due_tick` columns: signed-ceiling parity with
`nora.domain.ticks`, and that the snapshot seed's insert never names a
generated column (the independent review's BLOCKER — an explicit insert
into `due_tick` is a `GeneratedAlwaysError`, so the seed died on every
migrated row).

Both run against the migrated `clean_pg_url` schema, not a model of it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.domain.ticks import due_tick_of_at, due_tick_of_ms
from nora.infra.sql import create_sql_engine
from nora.infra.sql.schema import REMINDERS, STIMULUS
from nora.infra.sql.snapshot_at import _insert_sql, _target_columns

NOW_MS = 1_790_000_000_000


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@pytest.mark.fresh_database
async def test_the_stimulus_due_tick_column_computes_the_signed_ceiling(
    clean_pg_url: str,
) -> None:
    """`ceil(due_ms / 60000)` for SIGNED values: Postgres integer division
    truncates toward zero, so `(due_ms + 59999) / 60000` is wrong below the
    epoch (a pre-epoch deadline) and can overflow above it. The generated
    expression must equal `due_tick_of_ms` on every edge."""
    cases = [-2 * 60_000, -60_001, -60_000, -1, 0, 1, 59_999, 60_000,
             60_001, NOW_MS, NOW_MS + 1]
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            for i, due in enumerate(cases):
                await conn.execute(STIMULUS.insert().values(
                    unit="nora", kind="self", item="self", ref=f"ceil:{i}",
                    at_ms=0, due_ms=due, recorded_at=_at(0),
                ))
            # An undated row: NULL in, NULL out.
            await conn.execute(STIMULUS.insert().values(
                unit="nora", kind="message", item="message", ref="ceil:null",
                at_ms=0, due_ms=None, recorded_at=_at(0),
            ))
            rows = (await conn.execute(
                sa.select(STIMULUS.c.due_ms, STIMULUS.c.due_tick)
            )).all()
    finally:
        await engine.dispose()
    for due_ms, due_tick in rows:
        expected = None if due_ms is None else due_tick_of_ms(due_ms)
        assert due_tick == expected, (due_ms, due_tick, expected)


@pytest.mark.fresh_database
async def test_the_reminder_due_tick_column_computes_the_signed_ceiling(
    clean_pg_url: str,
) -> None:
    """The timestamptz spelling of the same rule — pre-epoch and boundary
    `due_at` instants included — equals `due_tick_of_at`."""
    cases = [-60_001, -60_000, -1, 0, 1, 60_000, NOW_MS, NOW_MS + 30_000]
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            for due in cases:
                await conn.execute(REMINDERS.insert().values(
                    id=uuid4(), requester_person_id="p", platform="signal",
                    conversation_id="c", conversation_type="group",
                    text=str(due), created_at=_at(0), due_at=_at(due),
                    state="pending",
                ))
            rows = (await conn.execute(
                sa.select(REMINDERS.c.text, REMINDERS.c.due_tick)
            )).all()
    finally:
        await engine.dispose()
    for text, due_tick in rows:
        assert due_tick == due_tick_of_at(_at(int(text))), (text, due_tick)


@pytest.mark.fresh_database
async def test_the_snapshot_insert_never_names_a_generated_column(
    clean_pg_url: str,
) -> None:
    """`_target_columns` feeds `_insert_sql`: with `due_tick` filtered out,
    an image that CARRIES the column (new image) and one that predates it
    (old image) both restore through the same loader — the instant is
    restored, Postgres recomputes the tick."""
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            stimulus_cols = await _target_columns(conn, "stimulus")
            reminder_cols = await _target_columns(conn, "reminders")
            assert "due_tick" not in {c[0] for c in stimulus_cols}
            assert "due_ms" in {c[0] for c in stimulus_cols}
            assert "due_tick" not in {c[0] for c in reminder_cols}
            assert "due_at" in {c[0] for c in reminder_cols}

            due = NOW_MS + 30_000
            new_image = {
                "id": 41, "unit": "nora", "kind": "self", "item": "self",
                "ref": "snap:new", "platform": None, "conversation_id": None,
                "person_id": None, "cues": [], "waits": 0, "at_ms": 0,
                "due_ms": due, "due_tick": due_tick_of_ms(due),
                "recorded_at": _at(0).isoformat(),
            }
            old_image = dict(new_image, id=42, ref="snap:old")
            del old_image["due_tick"]
            await conn.execute(
                sa.text(_insert_sql("stimulus", stimulus_cols)),
                {"rows": json.dumps([new_image, old_image])},
            )
            rows = (await conn.execute(
                sa.select(STIMULUS.c.id, STIMULUS.c.due_tick)
                .where(STIMULUS.c.ref.like("snap:%"))
            )).all()
    finally:
        await engine.dispose()
    assert sorted(rows) == [
        (41, due_tick_of_ms(due)), (42, due_tick_of_ms(due)),
    ]
