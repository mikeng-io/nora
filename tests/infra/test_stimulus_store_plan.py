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


async def test_the_pending_and_activity_reads_plan_onto_indexes(pg_url):
    engine = create_sql_engine(pg_url)
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
