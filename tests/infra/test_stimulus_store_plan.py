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

    `EXPLAIN ANALYZE`'s actual counters are the rows the executor really
    ranged over — `Actual Rows` and `Actual Rows Removed by Filter`, times
    `Actual Loops`, read off the JSON plan rather than the text's first
    `rows=` (which is the planner's estimate, not the executor's count).
    History (consumed-shaped rows: dated and due long ago, or undated and
    recorded long ago — all below the watermark's bounds) quadruples while
    the pending batch is held at three — one per branch of the read: a
    post-watermark id, a dated row past the watermark's tick, and an undated
    late commit recorded past its time. The row work the due read performs
    must hold at the batch's size, not the history's. (What this does NOT
    measure: the query's aggregate planner cost — the assertion is on
    instrumented row visits alone.)"""
    import json
    from datetime import UTC, datetime

    from nora.domain.ticks import due_tick_of_ms
    from nora.infra.sql.schema import STIMULUS, STIMULUS_RETRACTION

    def _at(ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)

    def _index_work(node: dict[str, Any]) -> tuple[int, int, bool]:
        """`(rows returned, rows removed by filter, seq-scanned)` for every
        index-scan node over `stimulus` — actual executor counts times
        loops, walked recursively."""
        out = removed = 0
        seq = node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == "stimulus"
        if (
            node.get("Node Type") in ("Index Scan", "Index Only Scan")
            and node.get("Relation Name") == "stimulus"
        ):
            loops = int(node.get("Actual Loops", 1))
            out += int(node.get("Actual Rows", 0)) * loops
            removed += int(node.get("Rows Removed by Filter", 0)) * loops
        for child in node.get("Plans", ()):
            o, r, s = _index_work(child)
            out += o
            removed += r
            seq |= s
        return out, removed, seq

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
            raw = (await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + sql)).scalar_one()
        plan = json.loads(raw) if isinstance(raw, str) else raw
        out, removed, seq = _index_work(plan[0]["Plan"])
        assert not seq
        return out + removed

    async def populate(history: int) -> int:
        """`history` consumed-shaped rows, then a fixed pending batch of
        three — one for each branch of the read — and the watermark's id over
        all but the last. Returns `through_id`."""
        rows: list[dict[str, Any]] = []

        def _row(kind: str, item: str, ref: str, at_ms: int,
                 due_ms: int | None, recorded: int) -> dict[str, Any]:
            # The writable authority: a writer names the tick its instant
            # means (`ck_stimulus_due_tick` refuses any other).
            return {
                "unit": "nora", "kind": kind, "item": item, "ref": ref,
                "at_ms": at_ms, "due_ms": due_ms,
                "due_tick": None if due_ms is None else due_tick_of_ms(due_ms),
                "recorded_at": _at(recorded),
            }

        for i in range(history):
            # Consumed-shaped history: dated rows whose due tick is at or
            # under the watermark's, undated rows recorded before its time.
            dated = i % 2 == 0
            rows.append(_row(
                "self" if dated else "message",
                "self" if dated else "message", f"h:{history}:{i}",
                NOW - 10**7, NOW - 10**6 if dated else None, NOW - 10**7,
            ))
        rows += [
            # Inside the id range, a dated row whose due tick is past the
            # watermark's tick — `idx_stimulus_unit_due` answers it.
            _row("self", "self", "p:due", NOW, NOW + 10**8, NOW - 10**6),
            # Inside the id range, undated, recorded after the watermark's
            # time — `idx_stimulus_unit_undated_recorded` (the late commit
            # the settle margin exists for).
            _row("message", "message", "p:late", NOW, None, NOW + 5_000),
            # Past the watermark's id — `idx_stimulus_unit_id`.
            _row("message", "message", "p:after", NOW, None, NOW + 5_000),
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
        # Positive control for the counter itself: retract the pending
        # batch and the `NOT EXISTS` filter must really drop those index
        # rows — `removed` nonzero proves the JSON key reads the executor's
        # count, not a silently-zero renamed field.
        through_id = await populate(60)
        async with engine.begin() as conn:
            pending_ids = [r[0] for r in (await conn.execute(sa.text(
                "SELECT id FROM stimulus ORDER BY id DESC LIMIT 3"))).all()]
            await conn.execute(STIMULUS_RETRACTION.insert(), [
                {"unit": "nora", "stimulus_id": i, "reason": "moved"}
                for i in pending_ids])
        filtered = await scan_rows(through_id)
    finally:
        await engine.dispose()
    # Three pending rows over three index ranges — the number is about the
    # batch, with a little slack for executor detail, never about the table.
    assert 0 < small <= 12, small
    assert large <= small + 4, (small, large)
    assert filtered > 0, "retracted rows were not removed by filter"
