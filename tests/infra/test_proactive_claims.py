"""Claim and recover due work through the durable Postgres queue."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.app.proactive_poller import ProactiveLanding, ProactivePoller
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.cognitive_graph_schema import (
    GRAPH_STIMULUS,
    GRAPH_STIMULUS_CLAIM,
    NORA_ACT,
)
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory import Act, ActAuthority, ActEnvelope, Outcome
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.domain.acts import ActIdentity
from tests.fakes import SinkSender
from tests.infra.test_graph_debrief import CONTEXT, wiring
from tests.infra.test_retrieval_scope import AT, ROOM, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_situation_staleness import intention
from tests.integration.snapshot_rows import REVISE, ROWS

DUE = AT + timedelta(minutes=2)
BOUND = 30.0


async def seed_due(scenario, graph_id="scheduled", **fields):
    values = {"origin_room": f"signal:{ROOM}", "delivery_mode": "choose_moment", "trigger": {
        "kind": "time", "at": DUE.isoformat(), "zone": "UTC",
    }, **fields}
    await intention(scenario, graph_id, **values)
    await publish_due(scenario, graph_id)
    return graph_id


async def publish_due(scenario, graph_id):
    from nora.domain.graph_stimuli import GraphStimulus

    async with scenario.engine.begin() as conn:
        await SqlGraphStimuli.append_in(conn, GraphStimulus(
            UNIT, "intention_due", graph_id, DUE, "clock", occurrence=DUE.isoformat(),
        ))


async def claim_rows(scenario):
    async with scenario.engine.connect() as conn:
        return (await conn.execute(sa.select(GRAPH_STIMULUS_CLAIM))).mappings().all()


def slots():
    return ConversationSlots(maxlen=2, spawn=asyncio.create_task)


class Delivery:
    def __init__(self, store, acts, *, fails_first=False, together=None):
        self.store = store
        self.together = together
        self.acts = ActEnvelope(acts)
        self.fails_first = fails_first
        self.seen = []
        self.sender = SinkSender()

    async def retry_pending(self, unit, target):
        assert unit == UNIT and target.conversation_id == ROOM
        if self.together is not None:
            await self.together.wait()

    async def run(self, claim, target):
        self.seen.append(claim.pass_id)
        try:
            if self.fails_first and len(self.seen) == 1:
                raise RuntimeError("generated failure before delivery")
            context = replace(CONTEXT, pass_id=claim.pass_id, occasion="proactive")
            authority = ActAuthority(UNIT, intention=claim.due.intention, room=ROOM,
                                     pass_context=context)
            act = Act(ActIdentity("platform.reply", claim.pass_id, 0), "delivery",
                      f"signal:{ROOM}", authority, "unknown")
            await self.acts.perform(
                act, lambda: self.sender.send(target, "Generated reply"),
                lambda result: Outcome("done" if result.ok else "failed"),
            )
        finally:
            await self.store.settle(claim, DUE)


def poller(scenario, store, delivery, room_slots):
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    return ProactivePoller(store, room_slots, ProactiveLanding(scenario.policy, access, {"signal"}),
                           delivery, lambda: DUE, tick_seconds=1, timeout_seconds=BOUND)


async def test_two_pollers_claim_one_occurrence_once(scenario):
    await seed_due(scenario)
    store = SqlProactiveClaims(scenario.engine)
    _, acts = wiring(scenario)
    together = asyncio.Barrier(2)
    first, second = (Delivery(store, acts, together=together) for _ in range(2))
    await asyncio.gather(*(poller(scenario, store, turn, slots()).poll()
                           for turn in (first, second)))
    assert len(first.seen + second.seen) == 1
    assert first.sender.texts + second.sender.texts == ["Generated reply"]
    [row] = await claim_rows(scenario)
    assert row["state"] == "closed"


async def test_a_held_room_and_a_locked_stimulus_both_defer_the_claim(scenario):
    await seed_due(scenario)
    store = SqlProactiveClaims(scenario.engine)
    _, acts = wiring(scenario)
    room_slots = slots()
    key = ("signal", ROOM)
    room_slots.acquire(key)
    worker = poller(scenario, store, Delivery(store, acts), room_slots)
    await worker.poll()
    assert await claim_rows(scenario) == []
    room_slots.release_and_dispatch(key)
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.select(GRAPH_STIMULUS).with_for_update())
        await worker.poll()
    assert await claim_rows(scenario) == []
    await worker.poll()
    assert len(await claim_rows(scenario)) == 1


async def test_a_stimulus_for_an_ended_intention_cannot_be_claimed(scenario):
    await seed_due(scenario, state="ended", ending="dropped", ended_at=DUE)
    store = SqlProactiveClaims(scenario.engine)
    assert await store.due_intentions(DUE) == ()


async def test_a_due_intention_delivers_once_and_closes_only_on_a_reply_outcome(scenario):
    await seed_due(scenario)
    store = SqlProactiveClaims(scenario.engine)
    _, acts = wiring(scenario)
    delivery = Delivery(store, acts, fails_first=True)
    worker = poller(scenario, store, delivery, slots())
    await worker.poll()
    [failed] = await claim_rows(scenario)
    assert failed["state"] == "released" and delivery.sender.sent == []
    await worker.poll()
    [closed] = await claim_rows(scenario)
    assert closed["state"] == "closed" and closed["pass_id"] != failed["pass_id"]
    assert delivery.sender.texts == ["Generated reply"]
    await worker.poll()
    assert delivery.seen == [failed["pass_id"], closed["pass_id"]]
    async with scenario.engine.connect() as conn:
        assert list(await conn.scalars(sa.select(NORA_ACT.c.trigger_id))) == [closed["pass_id"]]


async def test_restart_releases_stale_claims_and_rejects_the_old_act_identity(scenario):
    await seed_due(scenario)
    store = SqlProactiveClaims(scenario.engine)
    [due] = await store.due_intentions(DUE)
    claim = await store.claim(due, DUE)
    assert claim is not None
    bound = timedelta(seconds=BOUND)
    await store.recover(DUE + bound / 2, bound)
    assert await store.due_intentions(DUE + bound / 2) == ()
    await store.recover(DUE + bound, bound)
    [recovered] = await claim_rows(scenario)
    assert recovered["state"] == "released"
    _, acts = wiring(scenario)
    context = replace(CONTEXT, pass_id=claim.pass_id, occasion="proactive")
    act = Act(ActIdentity("platform.reply", claim.pass_id, 0), "delivery",
              f"signal:{ROOM}", ActAuthority(UNIT, intention=due.intention, room=ROOM,
                                             pass_context=context), "unknown")
    assert not await acts.reserve(act)


def test_the_snapshot_fixture_covers_the_claim_key():
    name = "graph_stimulus_claim"
    table = GRAPH_STIMULUS_CLAIM
    assert set(table.primary_key.columns.keys()) <= ROWS[name](1).keys()
    assert REVISE[name][0] in table.c
