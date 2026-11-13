"""The pending read's COST, not its answer (AGENTS.md: «Instruments measure
the RESULT; something must measure the COST»).

`stimulus` is append-only and grows with every item of every room forever, so
the evaluator's per-tick read must be answered from indexes, never by
scanning a unit's history. The contract suite proves the answer; this proves
the plan and the row work: the read walks `stimulus_pending`, the
materialized «not permanently consumed» set — an appended row leaves it only
by a `closed` claim or a retraction — so every visited row is genuine
backlog and claimed/closed history is never scanned. With sequential scans
priced out, the pending read ranges `uq_stimulus_pending`, joins the
stimulus by its primary key, probes `idx_stimulus_claim_stimulus` for the
open-claim check, and the last-activity read probes its own index.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from nora.domain.ports import NewWakeOutcome
from nora.domain.stimuli import NewStimulus, NewWake, WakeGuard
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
            unit="nora", mode="live", now_ms=NOW + 1,
            stale_before_ms=None, limit=100))
        activity = await _plan(engine, activity_query(unit="nora"))
    finally:
        await engine.dispose()
    for plan in (pending, activity):
        assert "Seq Scan on stimulus" not in plan, plan
        assert "Seq Scan on stimulus_claim" not in plan, plan
    assert "Seq Scan on stimulus_pending" not in pending, pending
    assert "uq_stimulus_pending" in pending, pending
    assert "idx_stimulus_claim_stimulus" in pending, pending
    assert "idx_stimulus_unit_message_recorded" in activity, activity


def _stimulus(unit: str, ref: str, *, due_ms: int | None = None,
              at_ms: int = NOW) -> NewStimulus:
    return NewStimulus(
        unit=unit, kind="self" if due_ms is not None else "message",
        item="self" if due_ms is not None else "message", ref=ref, at_ms=at_ms,
        platform="signal", conversation_id="g1", person_id="p1",
        cues=(), waits=False, due_ms=due_ms,
    )


async def test_the_due_read_stays_on_the_pending_batch_as_history_grows(clean_pg_url):
    """The plan says WHICH index; this says HOW MUCH row work, measured on
    generated history rather than trusted from `LIMIT` (node 18's acceptance:
    read work on generated history growing around a fixed pending batch).

    The state is reached through the real writes — `append`, `record_wake`,
    `record_outcome`, `view` — never a hand-set cursor: the OLDEST row is a
    self stimulus due far in the future (it stays in `stimulus_pending`
    forever — the low-id shape that pinned the old residual floor); behind
    it, 2000→8000 rows are genuinely claimed by a wake that CLOSED and so
    deleted from the set; the fixed pending batch sits at the end.
    `EXPLAIN ANALYZE`'s actual counters are the rows the executor really
    ranged over — `Actual Rows`, `Rows Removed by Filter`, `Actual Loops`,
    read off the JSON plan on EVERY relation the read touches (the pending
    set, the stimulus join, the claim probe). The row work must hold at the
    batch's size, not the history's. (What this does NOT measure: the
    query's aggregate planner cost — the assertion is on instrumented row
    visits alone.)"""
    import json

    def _work(node: dict[str, Any]) -> tuple[dict[str, tuple[int, int, int]], int, set[str]]:
        """`{relation: (rows returned, rows removed, loops)}` for every scan
        node — actual executor counts — the plan's TOTAL removed work (a
        `Rows Removed by Join Filter` on a nested-loop node carries no
        relation name but is exactly the visit-then-drop cost this measures),
        and the relations seq-scanned, walked recursively."""
        work: dict[str, tuple[int, int, int]] = {}
        seq: set[str] = set()
        loops = int(node.get("Actual Loops", 1))
        removed_total = (
            int(node.get("Rows Removed by Filter", 0))
            + int(node.get("Rows Removed by Join Filter", 0))
        ) * loops
        relation = node.get("Relation Name")
        kind = node.get("Node Type")
        if relation:
            out = int(node.get("Actual Rows", 0))
            removed = int(node.get("Rows Removed by Filter", 0))
            prev = work.get(relation, (0, 0, 0))
            work[relation] = (prev[0] + out * loops, prev[1] + removed * loops,
                              prev[2] + loops)
            if kind == "Seq Scan":
                seq.add(relation)
        for child in node.get("Plans", ()):
            child_work, child_removed, child_seq = _work(child)
            for rel, counts in child_work.items():
                prev = work.get(rel, (0, 0, 0))
                work[rel] = tuple(
                    a + b for a, b in zip(prev, counts, strict=True))
            removed_total += child_removed
            seq |= child_seq
        return work, removed_total, seq

    engine = create_sql_engine(clean_pg_url)
    store = SqlStimulusStore(engine)

    async def scan_rows() -> dict[str, tuple[int, int, int]]:
        sql = str(pending_query(
            unit="nora", mode="live", now_ms=NOW + 10**9,
            stale_before_ms=None, limit=100,
        ).compile(dialect=postgresql.dialect(),
                  compile_kwargs={"literal_binds": True}))
        async with engine.connect() as conn:
            await conn.execute(sa.text("SET enable_seqscan = off"))
            raw = (await conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + sql)).scalar_one()
        plan = json.loads(raw) if isinstance(raw, str) else raw
        work, removed, seq = _work(plan[0]["Plan"])
        assert not seq, plan
        return work, removed

    async def populate(history: int) -> int:
        """Oldest: one future-due self row. Then `history` rows genuinely
        claimed by a wake that CLOSED. Then the fixed pending batch of two.
        Returns the batch's statement count for `view` and `record_wake`."""
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "TRUNCATE stimulus, cognition_wake, cognition_wake_outcome, "
                "stimulus_retraction, stimulus_claim, stimulus_pending "
                "RESTART IDENTITY"
            ))
        # First read materializes the (nora, live) pending set — empty now;
        # every append below lands its pending row through the real write.
        await store.view(unit="nora", mode="live", now_ms=NOW, limit=1)
        future = await store.append(
            stimulus=_stimulus("nora", "p:future", due_ms=NOW + 10**10),
            recorded_at_ms=NOW,
        )
        claimed = [future]
        for i in range(history):
            sid = await store.append(
                stimulus=_stimulus("nora", f"h:{history}:{i}", due_ms=NOW - 10**6),
                recorded_at_ms=NOW - 10**7,
            )
            assert sid is not None
            claimed.append(sid)
        wid = await store.record_wake(wake=NewWake(
            unit="nora", mode="live", reason="settled", from_stimulus_id=0,
            through_stimulus_id=claimed[-1], stimulus_count=history,
            eligible_count=history, oldest_pending_ms=None, cut_cooldown=False,
            at_ms=NOW - 10**5, claimed_ids=tuple(s for s in claimed if s != future),
        ), recorded_at_ms=NOW - 10**5)
        assert wid is not None
        await store.record_outcome(outcome=NewWakeOutcome(
            wake_id=wid, unit="nora", outcome="closed", at_ms=NOW - 10**4,
        ), recorded_at_ms=NOW - 10**4)
        for ref in ("p:a", "p:b"):
            sid = await store.append(
                stimulus=_stimulus("nora", ref), recorded_at_ms=NOW + 5_000,
            )
            assert sid is not None
        async with engine.begin() as conn:
            await conn.execute(sa.text("ANALYZE stimulus"))
            await conn.execute(sa.text("ANALYZE stimulus_pending"))
            await conn.execute(sa.text("ANALYZE stimulus_claim"))
        statements = 0

        @sa.event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _count(*args: Any) -> None:
            nonlocal statements
            statements += 1

        view = await store.view(unit="nora", mode="live", now_ms=NOW + 10**9,
                                limit=2)
        assert [s.ref for s in view.pending] == ["p:a", "p:b"]
        wid2 = await store.record_wake(wake=NewWake(
            unit="nora", mode="live", reason="settled", from_stimulus_id=0,
            through_stimulus_id=view.pending[-1].id, stimulus_count=2,
            eligible_count=2, oldest_pending_ms=None, cut_cooldown=False,
            at_ms=NOW + 10**9 + 1, tracked=True,
            claimed_ids=tuple(s.id for s in view.pending),
        ), recorded_at_ms=NOW + 10**9 + 1,
            # TRACKED, like a real evaluator wake: its claims stay OPEN (no
            # outcome is written) — so the batch keeps its pending rows and
            # the measured read runs the claim anti-join probes for real.
            guard=WakeGuard(last_wake_id=wid, last_outcome="closed"))
        assert wid2 is not None
        sa.event.remove(engine.sync_engine, "before_cursor_execute", _count)
        return statements

    try:
        small_statements = await populate(2_000)
        small, small_removed = await scan_rows()
        large_statements = await populate(8_000)
        large, large_removed = await scan_rows()
    finally:
        await engine.dispose()
    # `view` + `record_wake` run the SAME bounded set of statements however
    # much closed history sits under the mark — and the executor's row work
    # says the same: every relation's returned/removed/loop counts hold at
    # the batch (the future-due row and the two pending rows, plus the
    # per-row claim probes), never the history's size.
    assert small_statements == large_statements > 0
    relations = {"stimulus_pending", "stimulus", "stimulus_claim",
                 "cognition_wake", "cognition_wake_outcome"}
    for relation in relations:
        s_out, s_removed, s_loops = small.get(relation, (0, 0, 0))
        l_out, l_removed, l_loops = large.get(relation, (0, 0, 0))
        assert l_out <= s_out + 2 and l_loops <= s_loops + 2, (
            relation, (s_out, s_removed, s_loops), (l_out, l_removed, l_loops),
        )
        assert l_removed <= s_removed + 2, (
            relation, (s_out, s_removed, s_loops), (l_out, l_removed, l_loops),
        )
    # The bound itself, proved three ways at both history sizes: the pending
    # index emits only the set's own rows (sentinel + future-due + the batch —
    # the open claim on the batch suppresses it, the eligibility predicate
    # drops the future row); every emitted row then costs exactly one stimulus
    # probe (`loops` — the per-row VISIT count — never grows with the claimed
    # history the set hides); and the removed work is measured, not ignored:
    # visit-then-drop shows as `Rows Removed by [Join] Filter` wherever
    # Postgres attributes it, and stays at the set's size.
    for label, work, removed in (("small", small, small_removed),
                                 ("large", large, large_removed)):
        pending = work.get("stimulus_pending", (0, 0, 0))
        stimulus = work.get("stimulus", (0, 0, 0))
        assert 0 < pending[0] <= 4, (label, work)
        assert stimulus[2] == pending[0], (
            "every pending row probes exactly one stimulus", label, work,
        )
        assert removed <= 4, (label, removed, work)
