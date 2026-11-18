"""Observe's stimulus producers through the registered rows they write."""

# ruff: noqa: F811 - pytest resolves the imported fixture names.

from dataclasses import replace
from typing import cast

import pytest
import sqlalchemy as sa

from nora.domain.graph_stimuli import GraphStimulus, StimulusKind
from nora.domain.models import Quote
from nora.infra.sql.aggregate_crossings import SqlAggregateCrossings
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, NORA_ACT_OUTCOME, REGISTRY
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import epoch_ms
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.schema import MESSAGES, PLATFORM_OBSERVATION, PRESENCE_STAYS, STIMULUS
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.domain.acts import Outcome, Verdict
from nora.memory.domain.admission_proposals import Retire, Revise, Scope
from tests.infra.test_act_envelope import _act
from tests.infra.test_aggregate_registration import (
    _UNITS,
    _build_mobility,
    _rollup_registration,
)
from tests.infra.test_clock import (
    test_each_clock_kind_publishes_once_at_its_authoritative_instant,  # noqa: F401
)
from tests.infra.test_cognitive_graph_admission import (
    CLAIM_PROPOSAL,
    PASS,
    _rows,
    _service,
)
from tests.infra.test_cognitive_graph_admission import (
    engine as admitted_engine,  # noqa: F401
)
from tests.infra.test_item_registration import (
    _AT,
    _MS,
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    _Writer,
    engine,  # noqa: F401
)


@pytest.mark.parametrize("cue", ["mention", "reply", "dm", "self_reference", "plain"])
async def test_a_record_item_gets_one_classified_stimulus(engine, cue):
    writer = _Writer(engine, _registration())
    ids = frozenset({"generated-nora"})
    record = SqlRecordStore(engine, registration=writer.registration, self_ids=lambda _: ids)
    message = writer._message()
    nora = replace(message, sender_id="generated-nora", timestamp=_MS - 1)
    await record.append(message=nora, is_nora=True)
    if cue == "mention":
        message = replace(message, mentions=ids)
    elif cue == "reply":
        message = replace(message, quote=Quote("generated-nora", "", _MS - 1))
    elif cue == "dm":
        message = replace(message, conversation_type="dm")
    elif cue == "self_reference":
        message = replace(message, body="I prefer tea.")
    row_id = await record.append(message=message, person_id=_PERSON)
    gram = (await _rows(engine, MESSAGES))[row_id]
    expected = {
        "plain": (), "self_reference": ("self_reference",),
    }.get(cue, ("addresses_nora",))
    assert await SqlGraphStimuli(engine).read(_UNIT) == (GraphStimulus(
        _UNIT, "strong_cue" if expected else "new_item", gram["graph_id"], _AT,
        "messages", _ROOM, _PERSON, expected,
    ),)
    async with engine.connect() as conn:
        assert await conn.scalar(sa.select(sa.func.count()).select_from(STIMULUS)) == 0


async def test_source_replay_keeps_one_item_stimulus(engine):
    writer = _Writer(engine, _registration())
    await writer.write("platform_observation")
    await writer.write("platform_observation")
    (row,) = (await _rows(engine, PLATFORM_OBSERVATION)).values()
    assert await SqlGraphStimuli(engine).read(_UNIT) == (
        GraphStimulus(_UNIT, "new_item", row["graph_id"], row["occurred_at"],
                      "platform_observation", _ROOM, _PERSON),
    )


@pytest.mark.parametrize("verdict", ["done", "uncertain", "failed"])
async def test_only_failed_act_outcome_gets_a_stimulus(engine, verdict):
    act = _act()
    store = SqlActStore(engine, _registration())
    assert await store.reserve(act)
    await store.observe(act, Outcome(cast(Verdict, verdict)))
    await store.observe(act, Outcome(cast(Verdict, verdict)))
    (outcome,) = (await _rows(engine, NORA_ACT_OUTCOME)).values()
    expected = (GraphStimulus(
        act.authority.unit, "failed_act", outcome["graph_id"], outcome["occurred_at"],
        "nora_act_outcome", act.authority.room, act.authority.person,
    ),) if verdict == "failed" else ()
    assert await SqlGraphStimuli(engine).read(act.authority.unit) == expected


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("dependent", [False, True])
async def test_admitted_revision_or_retirement_names_every_dependent(
    admitted_engine, retire, dependent,
):
    engine = admitted_engine
    graph = REGISTRY
    svc = _service(engine)
    original = CLAIM_PROPOSAL
    result = await svc.admit((original,), PASS, ("item-1",))
    assert result.outcomes[0].id == original.id
    edges = [("draws_on", "generated-dependent-a"), ("rests_on", "generated-dependent-b")]
    deps = tuple(source for _, source in edges) if dependent else ()
    if dependent:
        async with engine.begin() as conn:
            for edge_type, source in edges:
                await conn.execute(sa.insert(graph).values(id=source, unit="u", kind="claim"))
                edge = f"edge-{source}"
                await conn.execute(sa.insert(graph).values(id=edge, unit="u", kind="edge"))
                await conn.execute(sa.insert(EDGE).values(
                    id=edge, kind="edge", unit="u", type=edge_type, basis="committed",
                    src_id=source, dst_id=original.id, writer="fixture",
                    exposure="internal", state="live",
                ))
    revision = "generated-revision"
    proposal = Retire(
        id=revision, base=original.id, basis="inferred", scope=Scope("u"),
        reasoning="deduced", confidence="high",
    ) if retire else Revise(base=original.id, replacement=replace(original, id=revision))
    args = ((proposal,), PASS, ("item-1", original.id))
    admitted = await svc.admit(*args)
    assert (admitted.retirements if retire else admitted.revisions)[0].id == revision
    await svc.admit(*args)
    row = (await _rows(engine, CLAIM))[revision]
    expected = (GraphStimulus(
        "u", "dependency", original.id, row["asserted_at"], "admission",
        room=row["origin_room"], revision_id=revision,
        dependents=deps, occurrence=revision,
    ),) if dependent else ()
    assert await SqlGraphStimuli(engine).read("u") == expected


async def test_crossing_references_the_registered_aggregate_without_an_intention(engine):
    build = 1
    await _build_mobility(engine, build, _rollup_registration())
    stay_store = SqlMobilityStore(engine, build_id=build)
    rows = (await _rows(engine, PRESENCE_STAYS)).values()
    (row,) = (r for r in rows if r["unit"] == _UNITS[0])
    stays = await stay_store.stays_for_subject(subject_id=row["subject_id"])
    stimuli = SqlGraphStimuli(engine)
    producer = SqlAggregateCrossings(engine)
    await producer.publish(build, ())
    assert await stimuli.read(_UNITS[0]) == ()
    await producer.publish(build, stays)
    await producer.publish(build, stays)
    at = epoch_ms(row["ended_ts"])
    assert at is not None
    assert await stimuli.read(_UNITS[0]) == (
        GraphStimulus(_UNITS[0], "aggregate_crossing", row["graph_id"], at, "presence_stays",
                      person=row["person_id"], cues=("baseline_crossed",),
                      occurrence=at.isoformat()),
    )
    async with engine.connect() as conn:
        assert await conn.scalar(sa.text("SELECT count(*) FROM intention")) == 0
        assert await conn.scalar(sa.text("SELECT count(*) FROM cognition_session")) == 0


@pytest.mark.parametrize(
    "kind", ["watched_source_changed", "absence", "verdict", "presence_change"],
)
async def test_store_round_trips_declared_kind_within_its_unit(engine, kind):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(REGISTRY).values(id="generated-row", unit=_UNIT, kind="item"))
    store = SqlGraphStimuli(engine)
    stimulus = GraphStimulus(_UNIT, cast(StimulusKind, kind), "generated-row", _AT, "fixture")
    await store.append(stimulus)
    await store.append(stimulus)
    assert await store.read(_UNIT) == (stimulus,)
    assert await store.read("generated-other-unit") == ()
