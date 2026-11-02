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
    records = SqlComprehendRecords(engine, media_join=None,
        rooms_by_unit={"u1": [("signal", "c1"), ("signal", "c2")]},
        persons_by_unit={"u1": {"p1"}})
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
        (PERSON.key,), Walk("u1", frozenset({"p1"}), valid_at=NOW, known_at=NOW))
    rels = {e.relation.id for e in at_t.relations}
    assert "r2" not in rels and "r1" in rels
    at_later = await graph.walk(
        (PERSON.key,), Walk("u1", frozenset({"p1"}),
                            valid_at=NOW + timedelta(hours=2)))
    assert "r2" in {e.relation.id for e in at_later.relations}


# -- 4. every admitted item gets its own receipt -------------------------------


@pytest.mark.asyncio
async def test_multi_item_receipt_covers_each_admitted_work(engine):
    from types import SimpleNamespace

    from nora.agent.fetch_watch import GraphFetched
    from nora.app.cognition_memory import WakeMemory
    from nora.domain.memory_graph import Source
    from nora.infra.sql.schema import EXTRACTION, EXTRACTION_CLAIM

    async with engine.begin() as conn:
        await _insert_message(conn, 1, BASE_MS)
        await _insert_message(conn, 2, BASE_MS)
        await conn.execute(MESSAGES.update().where(MESSAGES.c.id == 2).values(
            conversation_id="c2"))
    records = SqlComprehendRecords(engine, media_join=None,
        rooms_by_unit={"u1": [("signal", "c1"), ("signal", "c2")]},
        persons_by_unit={"u1": {"p1"}})
    memory = WakeMemory(graph=SqlMemoryGraph(engine), records=records,
                        metrics=None, content_checks=False)
    items = {}
    for sid in (1, 2):
        work = WorkItem(f"stimuli:{sid}", ("stimulus", str(sid)), "u1", f"h{sid}", NOW)
        row = {"id": sid, "body": f"body {sid}", "ts": BASE_MS,
               "person_id": "p1", "mentions": []}
        items[sid - 1] = ComprehendItem(work,
            (Source(("message", str(sid)), "record", "u1", ("p1",), row["body"]),),
            "signal", f"c{sid}", snapshot={"messages": [row], "context": [],
                "source_keys": {str(sid): ["message", str(sid)]},
                "stimulus_inputs": {str(sid): f"source-{sid}"}})
    state = SimpleNamespace(unit="u1", session_id=10, historical=True,
        memory_items=items, graph_fetched=GraphFetched(), shown_keys=set(),
        admitted_indices=set(), admitted_relations={}, admitted_evidence=[])
    claims = []
    for ids in ([1], [1, 2]):
        claims.append({"about": [], "what": " and ".join(f"body {i}" for i in ids),
            "happened_at": {"source_id": 1, "expression": None}, "where": None,
            "how": "stated", "source_ids": ids,
            "evidence": [{"source_id": i, "quote": f"body {i}"} for i in ids]})
    payload = {key: [] for key in ("entities", "relations", "evidence", "corrections")}
    payload["readings"] = claims
    for _ in range(2):
        result = await memory.propose(state, items=[0, 1], payload=payload)
        assert result["status"] == "ok"
    async with engine.connect() as conn:
        extracts = (await conn.execute(sa.select(EXTRACTION))).mappings().all()
        saved = (await conn.execute(sa.select(EXTRACTION_CLAIM)
            .order_by(EXTRACTION_CLAIM.c.id))).mappings().all()
        pairs = await backfill_sql.readings_done(conn, ["input:u1:1", "input:u1:2"])
    assert len(extracts) == 1 and len(saved) == 2
    assert {row["extraction_id"] for row in saved} == {extracts[0]["id"]}
    assert [list(row["source_ids"]) for row in saved] == [[1], [1, 2]]
    assert {row["origin_room"] for row in saved} == {"c1"}
    assert extracts[0]["snapshot"]["source_keys"] == {
        "1": ["message", "1"], "2": ["message", "2"]}
    assert pairs == {("input:u1:1", "source-1"), ("input:u1:2", "source-2")}


@pytest.mark.asyncio
async def test_still_open_selects_graph_only_plan_and_excludes_closed_controls(engine):
    from nora.infra.sql.schema import ENTITY, RELATION

    async with engine.begin() as conn:
        await conn.execute(ENTITY.insert(), [
            {"id": "p1", "kind": "person", "unit": "u1", "name": "Generated Person"},
            {"id": "e1", "kind": "event", "unit": "u1", "name": "Generated Event"},
        ])
        rows = []
        for rid, unit, valid_to, status in (
            ("open-plan", "u1", None, "current"),
            ("closed-plan", "u1", NOW - timedelta(seconds=1), "current"),
            ("retired-plan", "u1", None, "retired"),
            ("foreign-plan", "u2", None, "current"),
        ):
            rows.append({"id": rid, "src_kind": "person", "src_id": "p1",
                "dst_kind": "event", "dst_id": "e1", "rel": "plans",
                "layer": "notebook", "unit": unit, "persons": ["p1"],
                "asserted_at": NOW, "valid_from": NOW + timedelta(days=1),
                "valid_to": valid_to, "status": status, "written_by": "generated"})
        await conn.execute(RELATION.insert(), rows)
        selected = await backfill_sql.still_open(conn, "u1", BASE_MS)
    assert [row["id"] for row in selected["relations"]] == ["open-plan"]
    assert selected["relations"][0]["persons"] == ["p1"]
    assert selected["relations"][0]["dst"] == ["event", "e1"]
    assert selected["commitments"] == [] and selected["events"] == []
