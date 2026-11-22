"""Support counts distinct visible cited occasions, without reading claim content."""

# ruff: noqa: F811 - pytest resolves the imported engine fixture.

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import CLAIM_SUPPORT, EDGE
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph, visible_to_reads
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.schema import MESSAGES
from nora.memory.wire import claim_support, memory_intake
from tests.infra.rollup_fixtures import RollupFixture
from tests.infra.test_disputes import _claim
from tests.infra.test_item_registration import _AT, engine  # noqa: F401


async def _evidence(engine, items):
    record = RollupFixture(engine)
    graph = SqlCognitiveGraph(engine)
    await _claim(graph, "generated-claim", unit=record.unit)
    for platform, at, body in items:
        message_id = await record.message(platform, int(at.timestamp() * 1000), body=body)
        async with engine.connect() as conn:
            graph_id = await conn.scalar(sa.select(MESSAGES.c.graph_id).where(
                MESSAGES.c.id == message_id,
            ))
        await graph.create(EDGE, {
            "id": f"generated-cite-{message_id}", "unit": record.unit,
            "kind": "edge", "basis": "computed", "type": "cites", "state": "live",
            "writer": "generated", "exposure": "internal",
            "src_id": "generated-claim", "dst_id": graph_id,
        })
    return record


async def _tally(engine, unit):
    count = await claim_support(engine).rebuild(unit=unit, claim_id="generated-claim", at=_AT)
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(CLAIM_SUPPORT).where(
            CLAIM_SUPPORT.c.unit == unit, visible_to_reads(CLAIM_SUPPORT),
        ))).mappings().one()
    assert count == row["support_count"]
    return count, row["last_evidenced_at"]


@pytest.mark.parametrize(
    ("items", "expected", "last"),
    [
        pytest.param(
            [("signal", _AT, "first"), ("signal", _AT + timedelta(seconds=1), "second"),
             ("signal", _AT + timedelta(seconds=2), "third")],
            1, _AT, id="same-occasion",
        ),
        pytest.param(
            [("signal", _AT, "copy"), ("discord", _AT + timedelta(seconds=1), "copy")],
            1, _AT, id="same-content",
        ),
        pytest.param(
            [("signal", _AT, "first"), ("discord", _AT + timedelta(days=1), "second")],
            2, _AT + timedelta(days=1), id="different-occasions",
        ),
    ],
)
async def test_rebuild_counts_one_per_unique_occasion_and_content(engine, items, expected, last):
    record = await _evidence(engine, items)
    assert await _tally(engine, record.unit) == (expected, last)


async def test_tally_days_use_utc_and_roomless_items_share_an_occasion(engine):
    east = timezone(timedelta(hours=8))
    before = datetime(2026, 8, 25, 7, 59, tzinfo=east)
    after = datetime(2026, 8, 25, 8, 0, tzinfo=east)
    record = await _evidence(engine, [
        ("signal", before, "one"), ("signal", after, "two"),
        ("discord", after + timedelta(seconds=1), "three"),
    ])
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).values(origin_room=None))
    assert await _tally(engine, record.unit) == (2, after)


@pytest.mark.parametrize("method", ["tombstone", "hidden"])
async def test_rebuild_drops_cited_items_that_stop_being_visible(engine, method):
    record = await _evidence(engine, [
        ("signal", _AT, "first"), ("discord", _AT + timedelta(days=1), "second"),
    ])
    assert await _tally(engine, record.unit) == (2, _AT + timedelta(days=1))
    condition = MESSAGES.c.body == "second"
    if method == "tombstone":
        async with engine.begin() as conn:
            (expired,) = await tombstone_items(conn, MESSAGES, condition)
            # A stale source image must not make a tombstoned graph id visible.
            await conn.execute(sa.insert(MESSAGES).values(dict(expired)))
    else:
        async with engine.connect() as conn:
            target = await conn.scalar(sa.select(MESSAGES.c.graph_id).where(condition))
        await memory_intake(engine, record.registration).forget(
            unit=record.unit, author=record.people[0], targets=[target], at=_AT,
        )
    assert await _tally(engine, record.unit) == (1, _AT)


async def test_rebuild_selects_claim_metadata_without_claim_content(engine):
    record = await _evidence(engine, [("signal", _AT, "first")])
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.split()).lower())

    with _listen(engine, capture):
        assert await claim_support(engine).rebuild(
            unit=record.unit, claim_id="generated-claim", at=_AT,
        ) == 1
    claim_selects = [
        sql for sql in statements if sql.startswith("select") and " from claim " in sql
    ]
    assert claim_selects
    assert all(not re.search(r"\bclaim(?:_\d+)?\.(content|prediction|window_end|\*)", sql)
               for sql in statements)


@contextmanager
def _listen(engine, capture):
    sa.event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        yield
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", capture)
