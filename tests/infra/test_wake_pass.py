"""A wake only consumes a durable set of claims when its runner closes."""

import asyncio
from datetime import timedelta

import pytest

from nora.app.wake_debrief import WakeDebriefRetry
from nora.app.wake_poller import WakePoller
from nora.app.wake_rooms import WakeRoomSlots
from nora.config import CognitionConfig
from nora.engagement.slots import ConversationSlots
from nora.memory import WakeClosed, WakeFailed
from nora.memory.adapters.wake_claims import SqlWakeClaims
from nora.obs.metrics import Metrics
from nora.testing.scripted_wake_pass import ScriptedWakePass
from tests.infra.test_graph_debrief import wiring
from tests.infra.test_proactive_claims import claim_rows
from tests.infra.test_retrieval_scope import AT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_wake_claims import GROUP, ORIGIN, POLICY, stimulus


def worker(scenario, script, *, room_slots=None, debrief_wiring=None, clock=lambda: AT, **settings):
    if room_slots is None:
        room_slots = ConversationSlots(
            maxlen=2, spawn=asyncio.create_task, policy=scenario.policy,
        )
    if debrief_wiring is None:
        debrief_wiring, _ = wiring(scenario)
    retry = WakeDebriefRetry(
        debrief_wiring, debrief_wiring.store,
        WakeRoomSlots(scenario.policy, room_slots), room_slots,
    )
    runner = ScriptedWakePass(script)
    metrics = Metrics()
    store = SqlWakeClaims(scenario.engine)
    config = CognitionConfig(**{"quiet_seconds": 1, "cooldown_seconds": 0, **settings})
    return WakePoller(store, runner, retry, scenario.policy, metrics, clock, config=config), (
        runner, metrics,
    )


async def tick(poller):
    await poller.poll()
    await poller.drain()


async def test_a_closed_wake_closes_its_whole_set(scenario):
    for graph_id in ("first", "second"):
        await stimulus(scenario, graph_id)
    poller, (runner, _) = worker(scenario, {("first", "second"): WakeClosed()})
    await tick(poller)
    await tick(poller)
    [claim] = runner.seen
    assert len(claim.stimuli) == 2
    rows = await claim_rows(scenario)
    assert {(row["graph_id"], row["state"], row["pass_id"]) for row in rows} == {
        (graph_id, "closed", claim.pass_id) for graph_id in ("first", "second")
    }


@pytest.mark.parametrize("cause", ["error", "timeout", "bound", "cancelled"])
async def test_an_incomplete_wake_releases_and_counts(scenario, cause):
    await stimulus(scenario, "generated")

    async def outcome():
        if cause == "bound":
            return WakeFailed("bound")
        raise {"error": RuntimeError, "timeout": TimeoutError, "cancelled": asyncio.CancelledError}[
            cause
        ]()

    poller, (runner, _) = worker(scenario, {("generated",): outcome})
    await tick(poller)
    [row] = await claim_rows(scenario)
    assert len(runner.seen) == 1
    assert (row["state"], row["attempts"]) == ("released", 1)


async def test_the_third_counted_failure_parks_the_set(scenario):
    for graph_id in ("first", "second"):
        await stimulus(scenario, graph_id)
    poller, (runner, metrics) = worker(scenario, {("first", "second"): WakeFailed("bound")})
    for attempts in range(1, 4):
        await tick(poller)
        assert {(row["state"], row["attempts"]) for row in await claim_rows(scenario)} == {
            ("released" if attempts < 3 else "parked", attempts),
        }
    await tick(poller)
    assert len(runner.seen) == 3
    assert metrics.get("nora_wake_stimulus_parked_total") == 2


async def test_a_late_settlement_cannot_close_a_reclaimed_row(scenario):
    await stimulus(scenario, "generated")
    store = SqlWakeClaims(scenario.engine)
    first = await store.claim(GROUP, ORIGIN, AT, POLICY)
    later = AT + timedelta(seconds=31)
    await store.recover(later, timedelta(seconds=30))
    second = await store.claim(GROUP, ORIGIN, later, POLICY)
    assert first is not None and second is not None
    assert second.pass_id != first.pass_id
    await store.settle(first, WakeClosed(), later)
    [active] = await claim_rows(scenario)
    assert (active["state"], active["pass_id"]) == ("claimed", second.pass_id)
    await store.settle(second, WakeClosed(), later)
    [closed] = await claim_rows(scenario)
    assert closed["state"] == "closed"


async def test_a_restarted_poller_releases_stale_claims_uncounted(scenario):
    await stimulus(scenario, "generated")
    store = SqlWakeClaims(scenario.engine)
    claim = await store.claim(GROUP, ORIGIN, AT, POLICY)
    assert claim is not None
    poller, (runner, _) = worker(scenario, {}, clock=lambda: AT + timedelta(seconds=31),
                                 cooldown_seconds=10, session_timeout_seconds=30)
    await tick(poller)
    [released] = await claim_rows(scenario)
    assert (released["state"], released["attempts"], released["pass_id"]) == (
        "released", 0, claim.pass_id,
    )
    assert runner.seen == []


async def test_running_wakes_never_block_the_next_tick_or_exceed_capacity(scenario):
    for index in range(3):
        await stimulus(scenario, f"generated-{index}", unit=f"unit-{index}")
    started = asyncio.Queue()
    finish = asyncio.Event()
    now = AT

    async def blocked():
        started.put_nowait(None)
        await finish.wait()
        return WakeClosed()

    poller, (runner, _) = worker(
        scenario, {(f"generated-{index}",): blocked for index in range(3)},
        clock=lambda: now, max_concurrent_sessions=2, session_timeout_seconds=30,
    )
    try:
        await asyncio.wait_for(poller.poll(), 5)
        for _ in range(2):
            await asyncio.wait_for(started.get(), 5)
        now += timedelta(seconds=31)
        await asyncio.wait_for(poller.poll(), 5)
        rows = await claim_rows(scenario)
        assert len(rows) == len(runner.seen) == 2
        assert {row["state"] for row in rows} == {"released"}
    finally:
        finish.set()
        await poller.drain()
