"""Replay uses the shared wake runner with historical read and write bounds."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY, GRAPH_STIMULUS
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.memory import ActRefused, Refusal, WakeClosed, declare_act, parse_intention_operation
from nora.memory.app.act_scope import active_act_authority
from nora.memory.domain.admission_ports import Stored
from nora.memory.domain.admission_proposals import Revise
from nora.memory.wire import WAKE_HISTORY_TOOLS
from tests.infra.test_cognitive_graph_admission import CLAIM_PROPOSAL, PASS, PERSON, _rows, _service
from tests.infra.test_cognitive_graph_admission import engine as engine
from tests.infra.test_graph_debrief import opening
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_wake_runner import WakeModel, claim, observation, runner

HISTORICAL = replace(PASS, occasion="wake_history", clock=datetime(2024, 5, 2, tzinfo=UTC))


async def test_history_admits_understanding_and_stamps_its_evidence_clock(engine):
    await SqlCognitiveGraph(engine).create(ENTITY, {
        "id": "person-fixture", "unit": PASS.unit, "kind": "entity", "basis": "computed",
        "state": "admitted", "writer": "fixture", "exposure": "internal",
        "name": PERSON.name, "pointer_kind": "person", "pointer_ref": PERSON.person_id,
    })
    [outcome] = (await _service(engine).admit(
        (CLAIM_PROPOSAL,), HISTORICAL, ("item-1",),
    )).outcomes
    assert outcome == Stored(CLAIM_PROPOSAL.id)
    row = (await _rows(engine, CLAIM))[CLAIM_PROPOSAL.id]
    assert row["occasion"] == "wake_history"
    assert row["asserted_at"] == row["occurred_at"] == HISTORICAL.clock
    edges = (await _rows(engine, EDGE)).values()
    assert {edge["type"] for edge in edges} == {"cites", "about"}
    assert all(edge["asserted_at"] == edge["occurred_at"] == HISTORICAL.clock for edge in edges)


async def test_history_refuses_an_intention_open_and_a_lesson(engine):
    intention = parse_intention_operation(opening(), HISTORICAL)
    assert not isinstance(intention, Refusal)
    lesson = replace(CLAIM_PROPOSAL, part="lesson")
    result = await _service(engine).admit((intention, lesson), HISTORICAL, ("item-1",))
    assert result.outcomes == (Refusal("occasion_basis", "basis"), Refusal("occasion_part", "part"))
    assert not await _rows(engine, CLAIM)


async def test_live_admission_uses_database_time(engine):
    async with engine.connect() as conn:
        before = await conn.scalar(sa.select(sa.func.clock_timestamp()))
    [outcome] = (await _service(engine).admit((CLAIM_PROPOSAL,), PASS, ("item-1",))).outcomes
    assert outcome == Stored(CLAIM_PROPOSAL.id)
    row = (await _rows(engine, CLAIM))[CLAIM_PROPOSAL.id]
    async with engine.connect() as conn:
        after = await conn.scalar(sa.select(sa.func.clock_timestamp()))
    assert before <= row["asserted_at"] <= after
    assert row["occurred_at"] is None


@pytest.mark.parametrize("context,queued", [(HISTORICAL, False), (PASS, True)])
async def test_only_live_admission_queues_dependents(engine, context, queued):
    service = _service(engine)
    dependent = replace(CLAIM_PROPOSAL, id="dependent")
    initial = await service.admit((CLAIM_PROPOSAL, dependent), context, ("item-1",))
    assert all(isinstance(outcome, Stored) for outcome in initial.outcomes)
    graph = SqlCognitiveGraph(engine)
    await graph.create(EDGE, {
        "id": "dependent-edge", "unit": context.unit, "kind": "edge", "basis": "inferred",
        "state": "live", "writer": "fixture", "type": "draws_on",
        "src_id": dependent.id, "dst_id": CLAIM_PROPOSAL.id,
    })
    revision = Revise(base=CLAIM_PROPOSAL.id, replacement=replace(CLAIM_PROPOSAL, id="revision"))
    result = await service.admit((revision,), context, ("item-1", CLAIM_PROPOSAL.id))
    assert result.outcomes == (Stored("revision", base=CLAIM_PROPOSAL.id),)
    async with engine.connect() as conn:
        ids = tuple(await conn.scalars(sa.select(GRAPH_STIMULUS.c.revision_id).where(
            GRAPH_STIMULUS.c.kind == "dependency",
        )))
    assert ids == (("revision",) if queued else ())


async def test_history_surface_refuses_acts_and_offers_only_reads_and_proposal(scenario):
    await scenario.item("observed")

    def observe():
        authority = active_act_authority()
        assert authority is not None
        with pytest.raises(ActRefused, match="non_engagement_pass"):
            declare_act("platform.reply", "fixture-room", event="fixture", authority=authority)

    model = WakeModel([], observe)
    replay = runner(scenario, model, occasion="wake_history", offered=WAKE_HISTORY_TOOLS)
    assert await replay.run(claim(observation("observed"))) == WakeClosed()
    assert model.tools == {"graph_propose", "memory_read", "memory_search"}


async def test_source_ceiling_hides_future_items_without_marking_a_limit(scenario):
    from datetime import timedelta

    from nora.memory.domain.pass_declarations import declaration
    from nora.memory.domain.retrieval import RetrievalAnchors
    from tests.infra.test_retrieval_scope import AT, READ

    await scenario.item("before", at=AT - timedelta(seconds=1))
    await scenario.item("at")
    await scenario.item("after", at=AT + timedelta(seconds=1))
    service = scenario.service()
    anchors = RetrievalAnchors(graph_ids=("before", "at", "after"))
    live = await service.retrieve(READ, anchors, declaration("wake").bounds(AT))
    replay = await service.retrieve(
        READ, anchors, declaration("wake_history").bounds(AT, clock=AT),
    )

    assert tuple(row.graph_id for row in live.rows) == ("before", "at")
    assert (live.status, live.limits) == ("partial", ("time_window",))
    assert tuple(row.graph_id for row in replay.rows) == ("before", "at")
    assert (replay.status, replay.limits) == ("ok", ())
