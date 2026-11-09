"""The pending read's COST, not its answer (AGENTS.md: «Instruments measure
the RESULT; something must measure the COST»).

`stimulus` is append-only and grows with every item of every room forever, so
the evaluator's per-tick read must be answered from indexes, never by
scanning a unit's history. The contract suite proves the answer; this proves
the plan: with sequential scans priced out, each branch of the pending read
ranges over its own index, and the last-activity read probes its own. The
single predicate the pending read avoids (`NOT (id <= t AND coalesce(...)
<= m)`, under `ORDER BY id LIMIT`) plans as a walk of `idx_stimulus_unit_id`
from the start of the unit's history with the predicate as a filter — no Seq
Scan, and no use of the due or undated index — which is what reddens this.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.stimuli import activity_query, pending_query

NOW = 1_790_000_000_000


async def _plan(engine, stmt) -> str:
    sql = str(stmt.compile(dialect=postgresql.dialect(),
                           compile_kwargs={"literal_binds": True}))
    async with engine.connect() as conn:
        await conn.execute(sa.text("SET enable_seqscan = off"))
        rows = (await conn.exec_driver_sql("EXPLAIN " + sql)).all()
    return "\n".join(r[0] for r in rows)


async def test_the_pending_and_activity_reads_plan_onto_indexes(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        pending = await _plan(engine, pending_query(
            unit="nora", through_id=10, through_ms=NOW, now_ms=NOW + 1, limit=100))
        activity = await _plan(engine, activity_query(unit="nora"))
    finally:
        await engine.dispose()
    for plan in (pending, activity):
        assert "Seq Scan on stimulus" not in plan, plan
    for index in ("idx_stimulus_unit_id", "idx_stimulus_unit_due",
                  "idx_stimulus_unit_undated_recorded"):
        assert index in pending, pending
    assert "idx_stimulus_unit_message_recorded" in activity, activity


async def test_the_due_read_stays_on_the_pending_batch_as_history_grows(clean_pg_url):
    """The plan says WHICH index; this says HOW MUCH row work, measured on
    generated history rather than trusted from `LIMIT` (node 18's acceptance:
    read work on generated history growing around a fixed pending batch).

    `EXPLAIN ANALYZE`'s `rows=` per index scan is the rows the executor
    actually ranged over. History (consumed-shaped rows: dated and due long
    ago, or undated and recorded long ago — all below the watermark's bounds)
    quadruples while the pending batch is held at three — one per branch of
    the read: a post-watermark id, a dated row past the watermark's tick, and
    an undated late commit recorded past its time. The row work the due read
    performs must hold at the batch's size, not the history's."""
    import re
    from datetime import UTC, datetime

    from nora.infra.sql.schema import STIMULUS

    def _at(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)

    engine = create_sql_engine(clean_pg_url)
    through_ms, now_ms = NOW, NOW + 10**9

    async def scan_rows(through_id: int) -> int:
        sql = str(pending_query(
            unit="nora", through_id=through_id, through_ms=through_ms,
            now_ms=now_ms, limit=100,
        ).compile(dialect=postgresql.dialect(),
                  compile_kwargs={"literal_binds": True}))
        async with engine.connect() as conn:
            await conn.execute(sa.text("SET enable_seqscan = off"))
            plan = (await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, FORMAT TEXT) " + sql)).all()
        text = "\n".join(r[0] for r in plan)
        assert "Seq Scan on stimulus" not in text
        return sum(
            int(m.group(1)) for line in text.splitlines()
            if ("Index Scan" in line or "Index Only Scan" in line)
            and "stimulus" in line and "actual" in line
            for m in [re.search(r"rows=(\d+)", line)] if m
        )

    async def populate(history: int) -> int:
        """`history` consumed-shaped rows, then a fixed pending batch of
        three — one for each branch of the read — and the watermark's id over
        all but the last. Returns `through_id`."""
        rows: list[dict[str, Any]] = []
        for i in range(history):
            # Consumed-shaped history: dated rows whose due tick is at or
            # under the watermark's, undated rows recorded before its time.
            dated = i % 2 == 0
            rows.append({
                "unit": "nora", "kind": "self" if dated else "message",
                "item": "self" if dated else "message", "ref": f"h:{history}:{i}",
                "at_ms": NOW - 10**7, "due_ms": NOW - 10**6 if dated else None,
                "recorded_at": _at(NOW - 10**7),
            })
        rows += [
            # Inside the id range, a dated row whose due tick is past the
            # watermark's tick — `idx_stimulus_unit_due` answers it.
            {"unit": "nora", "kind": "self", "item": "self", "ref": "p:due",
             "at_ms": NOW, "due_ms": NOW + 10**8, "recorded_at": _at(NOW - 10**6)},
            # Inside the id range, undated, recorded after the watermark's
            # time — `idx_stimulus_unit_undated_recorded` (the late commit
            # the settle margin exists for).
            {"unit": "nora", "kind": "message", "item": "message", "ref": "p:late",
             "at_ms": NOW, "due_ms": None, "recorded_at": _at(NOW + 5_000)},
            # Past the watermark's id — `idx_stimulus_unit_id`.
            {"unit": "nora", "kind": "message", "item": "message", "ref": "p:after",
             "at_ms": NOW, "due_ms": None, "recorded_at": _at(NOW + 5_000)},
        ]
        async with engine.begin() as conn:
            await conn.execute(sa.text("TRUNCATE stimulus RESTART IDENTITY"))
            await conn.execute(STIMULUS.insert(), rows)
            await conn.execute(sa.text("ANALYZE stimulus"))
            return (await conn.execute(
                sa.text("SELECT max(id) - 1 FROM stimulus"))).scalar_one()

    try:
        small = await scan_rows(await populate(2_000))
        large = await scan_rows(await populate(8_000))
    finally:
        await engine.dispose()
    # Three pending rows over three index ranges — the number is about the
    # batch, with a little slack for executor detail, never about the table.
    assert 0 < small <= 12, small
    assert large <= small + 4, (small, large)
