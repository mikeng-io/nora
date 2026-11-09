"""The pending read's COST, not its answer (AGENTS.md: «Instruments measure
the RESULT; something must measure the COST»).

`stimulus` is append-only and grows with every item of every room forever, so
the evaluator's per-tick read must be answered from indexes, never by
scanning a unit's history. The contract suite proves the answer; this proves
the plan: consumption is a per-row `stimulus_claim` fact probed by index, and
the below-mark residual is a cursor (`pending_floor`), never a walk of the
unit's history and never a list of the backlog. With sequential scans priced
out, each branch of the pending read ranges `idx_stimulus_unit_id`, the claim
probe ranges `idx_stimulus_claim_stimulus`, and the last-activity read probes
its own index.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from nora.domain.ports import NewWakeOutcome
from nora.domain.stimuli import NewWake
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.stimuli import SqlStimulusStore, activity_query, pending_query

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
            unit="nora", mode="live", now_ms=NOW + 1, floor=0, through_id=10,
            stale_before_ms=None, limit=100))
        activity = await _plan(engine, activity_query(unit="nora"))
    finally:
        await engine.dispose()
    for plan in (pending, activity):
        assert "Seq Scan on stimulus" not in plan, plan
        assert "Seq Scan on stimulus_claim" not in plan, plan
    assert "idx_stimulus_unit_id" in pending, pending
    assert "idx_stimulus_claim_stimulus" in pending, pending
    assert "idx_stimulus_unit_message_recorded" in activity, activity


async def test_the_due_read_stays_on_the_pending_batch_as_history_grows(clean_pg_url):
    """The plan says WHICH index; this says HOW MUCH row work, measured on
    generated history rather than trusted from `LIMIT` (node 18's acceptance:
    read work on generated history growing around a fixed pending batch).

    `EXPLAIN ANALYZE`'s actual counters are the rows the executor really
    ranged over — `Actual Rows` and `Rows Removed by Filter`, times `Actual
    Loops`, read off the JSON plan rather than the text's first `rows=`
    (which is the planner's estimate, not the executor's count). History —
    rows actually CLAIMED by a wake that closed, all at or under the
    watermark — quadruples while the pending batch is held at two: an
    unclaimed row under the mark (the residual range reaches it from
    `pending_floor`) and an undated late commit past it. The row work must
    hold at the batch's size, not the history's. (What this does NOT measure:
    the query's aggregate planner cost — the assertion is on instrumented
    row visits alone.)"""
    import json
    from datetime import UTC, datetime

    from nora.domain.ticks import due_tick_of_ms
    from nora.infra.sql.schema import STIMULUS

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
    store = SqlStimulusStore(engine)

    async def scan_rows(through_id: int, floor: int) -> tuple[int, int]:
        sql = str(pending_query(
            unit="nora", mode="live", now_ms=NOW + 10**9, floor=floor,
            through_id=through_id, stale_before_ms=None, limit=100,
        ).compile(dialect=postgresql.dialect(),
                  compile_kwargs={"literal_binds": True}))
        async with engine.connect() as conn:
            await conn.execute(sa.text("SET enable_seqscan = off"))
            raw = (await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + sql)).scalar_one()
        plan = json.loads(raw) if isinstance(raw, str) else raw
        out, removed, seq = _index_work(plan[0]["Plan"])
        assert not seq
        return out, removed

    async def populate(history: int) -> tuple[int, int]:
        """`history` rows CLAIMED by a wake that closed (the consumed shape),
        then a fixed pending batch of two — an unclaimed row under the mark
        and an undated late commit past it. Returns `(through_id, floor)`."""
        rows: list[dict[str, Any]] = []
        for i in range(history):
            dated = i % 2 == 0
            rows.append({
                "unit": "nora",
                "kind": "self" if dated else "message",
                "item": "self" if dated else "message", "ref": f"h:{history}:{i}",
                "at_ms": NOW - 10**7,
                "due_ms": NOW - 10**6 if dated else None,
                "due_tick": None if not dated else due_tick_of_ms(NOW - 10**6),
                "recorded_at": _at(NOW - 10**7),
            })
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "TRUNCATE stimulus, cognition_wake, cognition_wake_outcome, "
                "stimulus_claim RESTART IDENTITY"
            ))
            await conn.execute(STIMULUS.insert(), rows)
        # One wake claims ALL of history and closes: the consumed shape.
        wid = await store.record_wake(wake=NewWake(
            unit="nora", mode="live", reason="settled", from_stimulus_id=0,
            through_stimulus_id=history, stimulus_count=history,
            eligible_count=history, oldest_pending_ms=None, cut_cooldown=False,
            at_ms=NOW - 10**5, claimed_ids=tuple(range(1, history + 1)),
        ), recorded_at_ms=NOW - 10**5)
        assert wid is not None
        await store.record_outcome(outcome=NewWakeOutcome(
            wake_id=wid, unit="nora", outcome="closed", at_ms=NOW - 10**4,
        ), recorded_at_ms=NOW - 10**4)
        async with engine.begin() as conn:
            await conn.execute(STIMULUS.insert(), [
                # Under the mark's `through`, never claimed — the residual's
                # floor lands on it.
                {"unit": "nora", "kind": "self", "item": "self", "ref": "p:due",
                 "at_ms": NOW, "due_ms": NOW + 10**8,
                 "due_tick": due_tick_of_ms(NOW + 10**8), "recorded_at": _at(NOW - 10**6)},
                # The late commit, stamped past the mark's time.
                {"unit": "nora", "kind": "message", "item": "message", "ref": "p:late",
                 "at_ms": NOW, "due_ms": None, "due_tick": None,
                 "recorded_at": _at(NOW + 5_000)},
            ])
            await conn.execute(sa.text("ANALYZE stimulus"))
            through = int((await conn.execute(sa.text(
                "SELECT max(id) FROM stimulus"))).scalar_one())
            # `record_wake` computes the floor on the NEXT wake over this
            # mark: the first live row under `through` no closed wake claimed.
            floor = int((await conn.execute(sa.text(
                "SELECT min(id) FROM stimulus s WHERE NOT EXISTS ("
                "SELECT 1 FROM stimulus_claim c WHERE c.stimulus_id = s.id)"
            ))).scalar_one())
            return through, floor

    try:
        through, floor = await populate(2_000)
        small_out, small_removed = await scan_rows(through, floor)
        through, floor = await populate(8_000)
        large_out, large_removed = await scan_rows(through, floor)
        # Positive control for the counter itself, independent of the totals:
        # a row the index scan visits but the eligibility FILTER drops must
        # register in `Rows Removed by Filter`. A future-due row above the
        # mark does exactly that — (a retraction would not prove it: Postgres
        # plans `live_stimulus`'s NOT EXISTS as an anti-join, whose rejects
        # never appear in the scan node's `Rows Removed by Filter`).
        async with engine.begin() as conn:
            await conn.execute(STIMULUS.insert(), [{
                "unit": "nora", "kind": "self", "item": "self", "ref": "p:future",
                "at_ms": NOW, "due_ms": NOW + 10**10,
                "due_tick": due_tick_of_ms(NOW + 10**10),
                "recorded_at": _at(NOW),
            }])
        filtered_out, filtered_removed = await scan_rows(through, floor)
    finally:
        await engine.dispose()
    # Two pending rows over two index ranges — the number is about the batch,
    # with a little slack for executor detail, never about the history.
    assert 0 < small_out <= 8, small_out
    assert large_out <= small_out + 2, (small_out, large_out)
    assert filtered_out <= small_out, (small_out, filtered_out)
    assert filtered_removed > 0, "not-yet-due rows were not removed by filter"
