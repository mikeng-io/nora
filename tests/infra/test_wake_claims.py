"""Claim each eligible room's stimuli under one reflection pass ID."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import sqlalchemy as sa

from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS_CLAIM
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.adapters.wake_claims import SqlWakeClaims
from nora.memory.domain.wake import WakeClosed, WakeGroup, WakePolicy
from tests.infra.test_proactive_claims import DUE, claim_rows, seed_due
from tests.infra.test_retrieval_scope import AT, OTHER_ROOM, ROOM, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario

ORIGIN = f"signal:{ROOM}"
OTHER_ORIGIN = f"signal:{OTHER_ROOM}"
GROUP = WakeGroup(UNIT, ORIGIN)
POLICY = WakePolicy(
    quiet_seconds=1, cue_quiet_seconds=1, batch_max=4, max_age_seconds=120,
    cooldown_seconds=0, max_concurrent_sessions=3, backoff_max_seconds=0,
)


async def stimulus(scenario, graph_id, *, kind="new_item", room=ORIGIN, age=5, unit=UNIT):
    await scenario.item(graph_id, unit=unit)
    async with scenario.engine.begin() as conn:
        await SqlGraphStimuli.append_in(conn, GraphStimulus(
            unit, kind, graph_id, AT - timedelta(seconds=age), "fixture", room=room,
        ))


async def test_two_evaluators_claim_one_set_once(scenario):
    await stimulus(scenario, "first")
    await stimulus(scenario, "second")
    claims = await asyncio.gather(
        *(SqlWakeClaims(scenario.engine).claim(GROUP, ORIGIN, AT, POLICY) for _ in range(2)),
        return_exceptions=True,
    )
    assert all(not isinstance(claim, BaseException) for claim in claims), claims
    [claimed] = [claim for claim in claims if claim is not None]
    rows = await claim_rows(scenario)
    assert {(row["graph_id"], row["claimant"], row["pass_id"]) for row in rows} == {
        (graph_id, "wake", claimed.pass_id) for graph_id in ("first", "second")
    }


async def test_two_evaluators_claim_one_set_across_disjoint_groups_in_one_unit(scenario):
    await stimulus(scenario, "first")
    await stimulus(scenario, "second", room=OTHER_ORIGIN)
    claims = await asyncio.gather(*(SqlWakeClaims(scenario.engine).claim(
        WakeGroup(UNIT, room), room, AT, POLICY,
    ) for room in (ORIGIN, OTHER_ORIGIN)))
    [claimed] = [claim for claim in claims if claim is not None]
    [row] = await claim_rows(scenario)
    assert row["pass_id"] == claimed.pass_id


async def test_intention_due_is_exclusive_to_proactive(scenario):
    await seed_due(scenario)
    await stimulus(scenario, "message", room=None)
    store = SqlWakeClaims(scenario.engine)
    claim = await store.claim(WakeGroup(UNIT, None), ORIGIN, DUE, POLICY)
    assert claim is not None
    assert [s.kind for s in claim.stimuli] == ["new_item"]
    proactive = SqlProactiveClaims(scenario.engine)
    [due] = await proactive.due_intentions(DUE)
    assert await proactive.claim(due, DUE) is not None
    assert {(row["kind"], row["claimant"]) for row in await claim_rows(scenario)} == {
        ("new_item", "wake"), ("intention_due", "proactive"),
    }


async def test_aggregate_crossing_only_rides_with_a_room_stimulus(scenario):
    await stimulus(scenario, "crossing", kind="aggregate_crossing", room=None)
    store = SqlWakeClaims(scenario.engine)
    assert await store.groups(AT) == ()
    assert await store.claim(WakeGroup(UNIT, None), ORIGIN, AT, POLICY) is None
    await stimulus(scenario, "message")
    assert await store.groups(AT) == (GROUP,)
    claim = await store.claim(GROUP, ORIGIN, AT, POLICY)
    assert claim is not None
    assert {(row["graph_id"], row["pass_id"]) for row in await claim_rows(scenario)} == {
        ("message", claim.pass_id), ("crossing", claim.pass_id),
    }


async def test_batches_drain_oldest_eligible_stimuli_first(scenario):
    for graph_id, age in (("middle", 6), ("future", -1), ("oldest", 7), ("newest", 5)):
        await stimulus(scenario, graph_id, age=age)
    store = SqlWakeClaims(scenario.engine)
    policy = replace(POLICY, batch_max=2)
    first = await store.claim(GROUP, ORIGIN, AT, policy)
    assert first is not None
    assert [s.graph_id for s in first.stimuli] == ["oldest", "middle"]
    await store.settle(first, WakeClosed(), AT)
    second = await store.claim(GROUP, ORIGIN, AT, policy)
    assert second is not None
    assert [s.graph_id for s in second.stimuli] == ["newest"]
    await store.settle(second, WakeClosed(), AT)
    assert await store.groups(AT) == ()


async def test_a_parked_row_is_never_reclaimed(scenario):
    await stimulus(scenario, "parked")
    store = SqlWakeClaims(scenario.engine)
    parked = await store.claim(GROUP, ORIGIN, AT, POLICY)
    assert parked is not None
    table = GRAPH_STIMULUS_CLAIM
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.update(table).where(table.c.pass_id == parked.pass_id)
                           .values(state="parked", settled=AT, attempts=3))
    await stimulus(scenario, "fresh")
    fresh = await store.claim(GROUP, ORIGIN, AT, POLICY)
    assert fresh is not None
    assert [s.graph_id for s in fresh.stimuli] == ["fresh"]


async def test_two_rooms_form_separate_claim_sets(scenario):
    await stimulus(scenario, "first")
    await stimulus(scenario, "second", room=OTHER_ORIGIN)
    store = SqlWakeClaims(scenario.engine)
    assert set(await store.groups(AT)) == {GROUP, WakeGroup(UNIT, OTHER_ORIGIN)}
    first = await store.claim(GROUP, ORIGIN, AT, POLICY)
    assert first is not None
    assert [s.graph_id for s in first.stimuli] == ["first"]
    await store.settle(first, WakeClosed(), AT)
    second = await store.claim(WakeGroup(UNIT, OTHER_ORIGIN), OTHER_ORIGIN, AT, POLICY)
    assert second is not None
    assert [s.graph_id for s in second.stimuli] == ["second"]

async def test_roomless_input_waits_and_counts_when_its_unit_has_no_home(scenario):
    from tests.infra.test_wake_pass import tick, worker

    await stimulus(scenario, "roomless", room=None)
    await stimulus(scenario, "room-bearing")
    script = {("room-bearing",): WakeClosed(), ("roomless",): WakeClosed()}
    poller, (runner, metrics) = worker(scenario, script)
    for _ in range(2):
        await tick(poller)
    assert [claim.reading_room for claim in runner.seen] == [ORIGIN]
    assert [row["graph_id"] for row in await claim_rows(scenario)] == ["room-bearing"]
    assert metrics.get("nora_errors_total", stage="wake_no_home_room") == 2

    scenario.policy = replace(scenario.policy, conversations=tuple(
        replace(room, home_room=True) if room.primary_id == ROOM else room
        for room in scenario.policy.conversations
    ))
    poller, (runner, _) = worker(scenario, script)
    await tick(poller)
    [claimed] = runner.seen
    assert claimed.group.room is None and claimed.reading_room == ORIGIN
