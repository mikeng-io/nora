"""Debrief retries resume from durable evidence and recorded operation identities."""

from dataclasses import replace
from datetime import timedelta

import sqlalchemy as sa

from nora.agent.egress import LeakGuard
from nora.doctrine.loader import Doctrine
from nora.engagement.stages import graph_debrief
from nora.infra.sql.cognitive_graph_schema import DEBRIEF_OBLIGATION, INTENTION
from nora.infra.sql.cognitive_graph_store import GraphWriteError, SqlCognitiveGraph
from nora.infra.sql.schema import MESSAGES
from nora.memory import DebriefObligation
from nora.memory.adapters.admission_rows import SqlAdmissionRows
from nora.memory.adapters.admission_store import SqlAdmissionStore
from nora.memory.app.admission import AdmissionService
from nora.memory.app.admission_identity import AdmissionIdentity
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_graph_debrief import CONTEXT, WALL, opening, run, send, wiring
from tests.infra.test_retrieval_scope import AT, ROOM, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.integration.snapshot_rows import REVISE, ROWS

JOURNAL = DEBRIEF_OBLIGATION
KEY = f"signal:{ROOM}"


async def journal(scenario, pass_id=CONTEXT.pass_id):
    async with scenario.engine.connect() as conn:
        return (await conn.execute(sa.select(JOURNAL).where(
            JOURNAL.c.unit == UNIT, JOURNAL.c.pass_id == pass_id,
        ))).mappings().one()


async def test_a_timeout_leaves_pending_and_the_next_slot_settles_without_resending(scenario):
    w, acts = wiring(scenario, TimeoutError(), [])
    witness = await send(acts)

    await run(w)

    row = await journal(scenario)
    assert row["state"] == "pending" and row["proposal_ids"] is None
    assert w.metrics.get("nora_debrief_total", outcome="timeout") == 1

    await graph_debrief.retry(w, UNIT, "signal", ROOM)
    assert (await journal(scenario))["state"] == "settled"
    assert [item.id for item in await acts.read_pass_items(UNIT, CONTEXT.pass_id)
            if item.kind == "send_outcome"] == [witness]
    assert len(w.client.prompts) == 2

    await graph_debrief.retry(w, UNIT, "signal", ROOM)
    assert len(w.client.prompts) == 2
    assert (await journal(scenario))["attempts"] == 2


async def test_the_slot_stops_after_three_failed_attempts(scenario):
    w, _ = wiring(scenario, TimeoutError(), TimeoutError(), TimeoutError())
    await run(w)
    for _ in range(3):
        await graph_debrief.retry(w, UNIT, "signal", ROOM)

    row = await journal(scenario)
    assert (row["state"], row["attempts"]) == ("pending", 3)
    assert len(w.client.prompts) == 3


async def test_an_uncertain_commit_is_read_back_by_id_before_a_retry(scenario):
    class LostAcknowledgement(SqlCognitiveGraph):
        async def write_admission(self, table, row, edges, **options):
            recorded = await journal(scenario)
            assert recorded["state"] == "pending"
            assert recorded["proposal_ids"] == [{"id": row["id"], "witness": None}]
            await super().write_admission(table, row, edges, **options)
            raise GraphWriteError(commit_uncertain=True)

    engine = scenario.engine
    identity = AdmissionIdentity(scenario.persons, scenario.policy)
    store = SqlAdmissionStore(SqlAdmissionRows(engine, identity), LostAcknowledgement(engine),
                              engine)
    service = AdmissionService(
        store, identity, LeakGuard(Doctrine((), "", "", "", "", 0), "fixture-canary"),
        content_checks=False,
    )
    w, _ = wiring(scenario, [opening()], service=service)

    await run(w)

    pending = await journal(scenario)
    assert pending["state"] == "pending"
    [ref] = pending["proposal_ids"]
    assert list(await _rows(engine, INTENTION)) == [ref["id"]]

    await graph_debrief.retry(w, UNIT, "signal", ROOM)

    assert (await journal(scenario))["state"] == "settled"
    assert list(await _rows(engine, INTENTION)) == [ref["id"]]
    assert len(w.client.prompts) == 1


async def test_retry_uses_latest_settle_before_the_original_debrief_began(scenario):
    begin = AT + timedelta(minutes=10)
    settle = AT + timedelta(minutes=4)
    w, _ = wiring(scenario, TimeoutError(), [], clock=lambda: begin)
    previous = DebriefObligation(UNIT, KEY, "earlier-pass", "called", None, AT)
    assert await w.store.begin(previous)
    await w.store.settle(previous, settle)
    await scenario.item("before-settle", at=settle - timedelta(seconds=1))
    await scenario.item("after-settle", at=settle + timedelta(seconds=1))
    await scenario.item("later-chat", at=begin + timedelta(minutes=1))

    await run(w)

    later = replace(previous, pass_id="intervening-pass",  # noqa: S106
                    created=begin + timedelta(minutes=1))
    assert await w.store.begin(later)
    await w.store.settle(later, begin + timedelta(minutes=2))
    await graph_debrief.retry(replace(w, clock=lambda: begin + timedelta(minutes=3)),
                              UNIT, "signal", ROOM)

    assert (await journal(scenario))["state"] == "settled"
    assert len(w.client.prompts) == 2
    for prompt in w.client.prompts:
        assert "after-settle" in prompt
        assert "before-settle" not in prompt and "later-chat" not in prompt


async def test_the_first_debrief_uses_its_saved_transcript_bounds(scenario):
    w, _ = wiring(scenario, TimeoutError(), [], clock=lambda: WALL)
    for graph_id in ("outside-window", "start-of-window", "end-of-window", "after-window"):
        await scenario.item(graph_id)
    async with scenario.engine.connect() as conn:
        records = dict((await conn.execute(
            sa.select(MESSAGES.c.graph_id, MESSAGES.c.id),
        )).all())
    window = tuple(type("MessageRef", (), {"id": records[name]})()
                   for name in ("start-of-window", "end-of-window"))
    from tests.infra.test_graph_debrief import AUTHORITY, MESSAGE

    await graph_debrief.run(w, AUTHORITY, MESSAGE, window)
    await graph_debrief.retry(w, UNIT, "signal", ROOM)

    [first, retry] = w.client.prompts
    assert first == retry
    assert "start-of-window" in first and "end-of-window" in first
    assert "outside-window" not in first and "after-window" not in first


async def test_snapshot_fixture_registers_the_debrief_journal(scenario):
    table = JOURNAL.name
    row = ROWS[table](0)
    column, revised = REVISE[table]
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.insert(JOURNAL).values(**row))
        await conn.execute(sa.update(JOURNAL).where(JOURNAL.c.pass_id == row["pass_id"]).values(
            {column: revised(0)},
        ))
        captured = await conn.scalar(sa.text(
            "SELECT count(*) FROM row_history WHERE table_name = 'debrief_obligation'",
        ))
    assert captured == 2
