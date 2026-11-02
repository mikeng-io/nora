"""Real-store proofs for the unified-backfill repair (PR525 findings):

1. A message hidden AFTER its evidence instant stays out of the as-of read
   — current withdrawal is never re-readable — while a never-hidden row at
   the same instant is still returned (positive control).
2. `fail_historical` receipts land under the isolated
   `backfill-stimulus:` key: five historical failures do not touch the
   live `stimulus:` failure counter the wake's parked rule reads.
3. The graph walk's `valid_at` now also bounds `asserted_at`: a relation
   asserted after the instant cannot leak in, one asserted before stays.
4. A multi-item proposal writes a `readings` receipt for EVERY admitted
   item (the combined call admitted them all).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.comprehend import ComprehendItem
from nora.domain.memory_graph import Walk, WorkItem
from nora.infra.sql import unified_backfill as backfill_sql
from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.schema import COMPREHEND_RESULT, MESSAGES
from tests.contract.test_memory_graph_contract import PERSON, REL, load
from tests.contract.test_memory_graph_contract import _graph_db as _graph_db

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BASE_MS = int(NOW.timestamp() * 1000)
HOUR = 3_600_000


@pytest.fixture
async def engine(_graph_db):
    e = create_sql_engine(_graph_db)
    async with e.begin() as conn:
        await conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
        await conn.execute(sa.text(
            "TRUNCATE relation_evidence, relation, entity, memory_work_item, "
            "memory_node, memory_edge, messages, message_edit, message_reaction, "
            "message_delete, comprehend_result, extraction, stimulus "
            "RESTART IDENTITY CASCADE"))
    try:
        yield e
    finally:
        await e.dispose()


def _message_row(mid: int, ts: int) -> dict[str, Any]:
    return {
        "id": mid, "platform": "signal", "conversation_id": "c1",
        "sender_id": "+1", "sender_name": "A", "ts": ts, "body": f"body {mid}",
        "is_nora": False, "delivery_status": "sent", "raw_json": "{}",
        "quoted_ts": None, "media_json": None, "person_id": "p1",
        "external_files_json": None,
    }


async def _insert_message(conn: Any, mid: int, ts: int) -> None:
    row = {c.name: v for c in MESSAGES.c
           for v in [_message_row(mid, ts).get(c.name)] if v is not None}
    await conn.execute(MESSAGES.insert().values(**row))


# -- 1. withdrawal is never re-readable, even as-of ----------------------------


@pytest.mark.asyncio
async def test_hidden_after_evidence_stays_out_of_as_of_read(engine):
    from nora.infra.sql.audit import SqlRecordStore
    from nora.infra.sql.record_query import SqlRecordQuery

    async with engine.begin() as conn:
        await _insert_message(conn, 1, BASE_MS)
        await _insert_message(conn, 2, BASE_MS)
    store = SqlRecordStore(engine)
    # Message 1 is withdrawn AFTER its evidence instant.
    assert await store.hide_message(message_id=1, now_ms=BASE_MS + HOUR)

    query = SqlRecordQuery(engine)
    historical = await query.by_refs(
        [("messages", 1), ("messages", 2)],
        [("signal", "c1")], as_of_ms=BASE_MS)
    named = {(h.table, h.row_id) for h in historical}
    # The withdrawn row stays out at EVERY instant — as-of cannot resurrect
    # what is hidden NOW. The control row at the same instant still reads.
    assert ("messages", 1) not in named
    assert ("messages", 2) in named


# -- 2. historical failures never poison live parking --------------------------


@pytest.mark.asyncio
async def test_five_historical_failures_do_not_park_live(engine):
    records = SqlComprehendRecords(engine)
    live_sid = 42
    for _ in range(backfill_sql.PARK_AFTER):
        await backfill_sql.fail_historical(engine, "u1", live_sid, None)
    # The LIVE parked counter (`WakeMemory.parked` →
    # `stimulus:{unit}:{sid}`) sees nothing — the receipts exist only under
    # the isolated historical key.
    assert await records.failures("u1", live_sid) == 0
    async with engine.connect() as conn:
        hist = await backfill_sql.failure_counts(conn, "u1", [live_sid])
    assert hist[live_sid] == backfill_sql.PARK_AFTER
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            COMPREHEND_RESULT.c.work_id, COMPREHEND_RESULT.c.part)
            .where(COMPREHEND_RESULT.c.part == "stimulus_failure"))).all()
    assert {r.work_id for r in rows} == {
        f"backfill-stimulus:u1:{live_sid}"}


# -- 3. walk bounds asserted_at ------------------------------------------------


@pytest.mark.asyncio
async def test_walk_omits_assertions_after_the_instant(engine):
    graph = SqlMemoryGraph(engine)
    # A relation asserted at T+1 with open-ended validity.
    future = replace(REL, id="r2", asserted_at=NOW + timedelta(hours=1))
    await load(graph, REL)       # asserted NOW, known by T
    await load(graph, future)    # asserted T+1, must not leak at T

    at_t = await graph.walk(
        (PERSON.key,), Walk("u1", frozenset({"p1"}), valid_at=NOW))
    rels = {e.relation.id for e in at_t.relations}
    assert "r2" not in rels and "r1" in rels
    at_later = await graph.walk(
        (PERSON.key,), Walk("u1", frozenset({"p1"}),
                            valid_at=NOW + timedelta(hours=2)))
    assert "r2" in {e.relation.id for e in at_later.relations}


# -- 4. every admitted item gets its own receipt -------------------------------


@pytest.mark.asyncio
async def test_multi_item_receipt_covers_each_admitted_work(engine):
    """The receipt contract the driver's done-check relies on: a combined
    `memory_propose` call admits N items; each lands a `readings` row under
    its OWN (work_id, input_hash) — `readings` is idempotent on the pair.
    Exercised at the store level: writing the same receipt twice keeps one
    row, and two distinct items keep two."""
    records = SqlComprehendRecords(engine)

    a = WorkItem("stimuli:1", ("stimulus", "(1,)"), "u1", "ha", NOW)
    b = WorkItem("stimuli:2,3", ("stimulus", "(2,3)"), "u1", "hb", NOW)
    item = ComprehendItem(
        work=a, sources=(), platform="signal", room="c1",
        snapshot={"messages": []})

    await records.readings(a, 10, item, [{"text": "t"}])
    await records.readings(b, 10, item, [{"text": "t"}])
    await records.readings(a, 10, item, [{"text": "t"}])  # idempotent

    async with engine.connect() as conn:
        pairs = await backfill_sql.readings_done(
            conn, ["stimuli:1", "stimuli:2,3"])
    assert ("stimuli:1", "ha") in pairs
    assert ("stimuli:2,3", "hb") in pairs
    async with engine.connect() as conn:
        n = (await conn.execute(sa.select(sa.func.count()).select_from(
            COMPREHEND_RESULT).where(
                COMPREHEND_RESULT.c.part == "readings"))).scalar()
    assert n == 2
