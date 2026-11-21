"""Reflection yields to a held engagement slot for the intention's policy room."""

import asyncio
from dataclasses import replace

import pytest

from nora.agent.egress import LeakGuard
from nora.app.wake_rooms import WakeRoomSlots
from nora.doctrine.loader import Doctrine
from nora.domain.identity import PlatformIdentity
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import admission as engagement
from nora.memory import Refusal, WakeFailed, parse_intention_operation
from nora.memory.domain.admission_ports import Stored
from nora.memory.domain.admission_proposals import AddEdge, CreateIntention, Link, Scope
from nora.memory.wire import admission
from nora.obs.metrics import Metrics
from nora.tag import Debounce
from tests.infra.test_debrief_obligation import journal, pending
from tests.infra.test_graph_debrief import CONTEXT, MESSAGE, opening, wiring
from tests.infra.test_proactive_claims import claim_rows
from tests.infra.test_retrieval_scope import ROOM
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_situation_staleness import intention
from tests.infra.test_wake_claims import ORIGIN, stimulus
from tests.infra.test_wake_pass import tick, worker

ALIAS = "generated-non-primary-room-id"
SUBJECT = "generated-intention"
WAKE = replace(CONTEXT, occasion="wake", writer="cognition")
KEY = ("signal", ROOM)


def with_alias(scenario):
    rooms = tuple(
        replace(room, ids=room.ids | {ALIAS}) if room.primary_id == ROOM else room
        for room in scenario.policy.rooms()
    )
    scenario.policy = replace(scenario.policy, conversations=rooms, _by_id={
        (room.platform, room_id): room for room in rooms for room_id in room.ids
    })


def slots(scenario):
    return ConversationSlots(maxlen=3, spawn=asyncio.create_task, policy=scenario.policy)


def service(scenario, room_slots):
    guard = LeakGuard(Doctrine((), "", "", "fixture", "", 0), "fixture-canary")
    return admission(
        scenario.engine, scenario.persons, scenario.policy, guard, content_checks=False,
        room_slots=WakeRoomSlots(scenario.policy, room_slots),
    )


async def revise(admitter, context=WAKE):
    proposal = parse_intention_operation(opening(op="revise", base=SUBJECT), context)
    assert not isinstance(proposal, Refusal)
    [outcome] = (await admitter.admit((proposal,), context, (SUBJECT,))).outcomes
    return outcome


async def test_a_held_room_refuses_wake_revisions_but_admits_debrief(scenario):
    await intention(scenario, SUBJECT, origin_room=ORIGIN)
    room_slots = slots(scenario)
    room_slots.acquire(KEY)
    admitter = service(scenario, room_slots)
    assert await revise(admitter) == Refusal("room_busy", "origin_room")
    assert isinstance(await revise(admitter, replace(WAKE, occasion="debrief")), Stored)


async def test_a_unit_scope_intention_is_never_busy(scenario):
    await intention(scenario, SUBJECT, origin_room=None)
    room_slots = slots(scenario)
    room_slots.acquire(KEY)
    assert isinstance(await revise(service(scenario, room_slots)), Stored)


async def test_an_intention_stored_under_an_alias_resolves_to_the_held_primary_slot(scenario):
    with_alias(scenario)
    await intention(scenario, SUBJECT, origin_room=f"signal:{ALIAS}")
    room_slots = slots(scenario)
    room_slots.acquire(KEY)
    assert await revise(service(scenario, room_slots)) == Refusal("room_busy", "origin_room")


async def test_an_unresolved_intention_room_is_refused(scenario):
    await intention(scenario, SUBJECT, origin_room="signal:unknown-fixture-room")
    room_slots = slots(scenario)
    assert await revise(service(scenario, room_slots)) == Refusal("unresolved_room", "origin_room")


async def test_engagement_alias_acquisition_blocks_a_wake_write_and_defers_debrief(scenario):
    with_alias(scenario)
    await intention(scenario, SUBJECT, origin_room=ORIGIN)
    await pending(scenario)
    room_slots = slots(scenario)
    admitter = service(scenario, room_slots)
    w, _ = wiring(scenario, [], service=admitter)
    poller, _ = worker(scenario, {}, room_slots=room_slots, debrief_wiring=w)

    result = engagement.run(
        message=replace(MESSAGE, conversation_id=ALIAS),
        identity=PlatformIdentity("signal", "fixture-nora"),
        debounce=Debounce(0), slots=room_slots, metrics=Metrics(),
    )
    assert result is engagement.Admission.RUN_NOW
    assert await revise(admitter) == Refusal("room_busy", "origin_room")
    await tick(poller)
    assert (await journal(scenario))["state"] == "pending"
    assert not w.client.prompts

    room_slots.release_and_dispatch(KEY)
    assert isinstance(await revise(admitter), Stored)
    await tick(poller)
    assert (await journal(scenario))["state"] == "settled"


@pytest.mark.parametrize("operation", ["drop", "close", "edge"])
async def test_all_intention_mutations_yield_to_the_held_origin_room(scenario, operation):
    await intention(scenario, SUBJECT, origin_room=ORIGIN)
    await scenario.item("generated-witness")
    room_slots = slots(scenario)
    room_slots.acquire(KEY)
    if operation == "edge":
        proposal = AddEdge(
            id="generated-edge", scope=Scope(WAKE.unit), basis="committed", type="cites",
            source=SUBJECT, target="generated-witness",
        )
    else:
        raw = {"op": operation, "base": SUBJECT}
        if operation == "close":
            raw["witness"] = "generated-witness"
        proposal = parse_intention_operation(raw, WAKE)
        assert not isinstance(proposal, Refusal)
    result = await service(scenario, room_slots).admit(
        (proposal,), WAKE, (SUBJECT, "generated-witness"),
    )
    assert result.outcomes == (Refusal("room_busy", "origin_room"),)


async def test_an_inline_edge_cannot_write_from_a_held_intention(scenario):
    await intention(scenario, SUBJECT, origin_room=ORIGIN)
    room_slots = slots(scenario)
    room_slots.acquire(KEY)
    admitter = service(scenario, room_slots)
    created = parse_intention_operation(opening(), WAKE)
    assert isinstance(created, CreateIntention)
    proposal = replace(created, links=(
        Link(type="serves", source=SUBJECT, target=created.id, basis="committed"),
    ))
    refused = await admitter.admit((proposal,), WAKE, (SUBJECT,))
    assert refused.outcomes == (Refusal("room_busy", "origin_room"),)
    room_slots.release_and_dispatch(KEY)
    admitted = await admitter.admit((proposal,), WAKE, (SUBJECT,))
    assert isinstance(admitted.outcomes[0], Stored)


async def test_a_room_busy_wake_releases_without_counting(scenario):
    await stimulus(scenario, "generated")
    poller, (runner, metrics) = worker(scenario, {("generated",): WakeFailed("room_busy")})
    await tick(poller)
    [row] = await claim_rows(scenario)
    assert len(runner.seen) == 1
    assert (row["state"], row["attempts"]) == ("released", 0)
    assert metrics.get("nora_wake_stimulus_parked_total") == 0
