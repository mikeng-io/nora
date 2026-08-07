"""The tick, with a fake clock and a stub `fire` — no model, no adapter, no
network. Every collaborator `HeartbeatLoop` has arrives through `__init__`,
including the clock, which is what makes the whole of §5 testable here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from nora.app.heartbeat import HeartbeatLoop
from nora.domain.reminders import Reminder, ReminderState
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.testing.reminders import FakeReminderStore

T0 = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
KEY = ("signal", "conv-1")


class _AllowAll:
    def is_allowed(self, *, platform, conversation_id, person_id) -> bool:
        return True


class _DenyAll:
    def is_allowed(self, *, platform, conversation_id, person_id) -> bool:
        return False


def _row(**kw) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles", "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group", "text": "洗衫",
        "created_at": T0 - timedelta(hours=1), "due_at": T0 - timedelta(minutes=1),
        "state": ReminderState.PENDING, "fired_at": None, "outcome": None,
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    base.update(kw)
    return Reminder(**base)


def _build(*, store, policy=None, fire=None, slots=None, metrics=None, clock=None):
    """One constructor for every test — the loop takes all collaborators
    through __init__, including the clock, so nothing here needs a real clock,
    a real model, or a real adapter."""
    fired: list[list[Reminder]] = []

    async def _default_fire(rows):
        fired.append(list(rows))
        return "sent"

    loop = HeartbeatLoop(
        store=store,
        # `is not None`, NOT `or`: ConversationSlots defines __len__, so a real
        # slots object with an empty queue is FALSY and `or` silently swaps it
        # for a fresh one — which hands every test its own private lock and
        # makes the held-slot test pass a turn it should have deferred.
        slots=slots if slots is not None else ConversationSlots(maxlen=16, spawn=lambda c: None),
        policy=policy or _AllowAll(),
        clock=clock or (lambda: T0),
        metrics=metrics or Metrics(),
        tick_seconds=60.0,
        fire=fire or _default_fire,
    )
    return loop, fired


async def test_a_due_row_fires_and_is_marked_done():
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    loop, fired = _build(store=store)

    await loop.tick()

    assert [r.id for r in fired[0]] == [row.id]
    assert (await store.get(row.id)).state is ReminderState.DONE
    assert (await store.get(row.id)).outcome == "sent"


async def test_a_row_not_yet_due_does_not_fire():
    store = FakeReminderStore()
    await store.create(_row(due_at=T0 + timedelta(minutes=1)))
    loop, fired = _build(store=store)

    await loop.tick()

    assert fired == []


async def test_rows_are_grouped_by_conversation_one_turn_each():
    """§5.2: a turn has one conversation scope — one Room, one policy decision,
    one Deliverer binding. Two conversations due in the same tick are two
    turns; sharing one would leak one room's reminder into the other."""
    store = FakeReminderStore()
    a1 = _row(conversation_id="conv-1")
    a2 = _row(conversation_id="conv-1")
    b1 = _row(conversation_id="conv-2")
    for r in (a1, a2, b1):
        await store.create(r)
    loop, fired = _build(store=store)

    await loop.tick()

    groups = {rows[0].conversation_id: {r.id for r in rows} for rows in fired}
    assert groups == {"conv-1": {a1.id, a2.id}, "conv-2": {b1.id}}


async def test_a_revoked_conversation_never_fires():
    """§5.1: policy.toml loads once at startup and a row may be 30 days old, so
    trust withdrawn in between must bite at fire time."""
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    metrics = Metrics()
    loop, fired = _build(store=store, policy=_DenyAll(), metrics=metrics)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_policy"
    assert metrics.get("nora_reminders_fired_total", outcome="failed_policy") == 1


async def test_a_held_slot_defers_to_the_next_tick():
    """§5.3: never shed, never wait. The row stays PENDING and the tick moves on
    — a reminder is not second-critical and the row persists."""
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    slots = ConversationSlots(maxlen=16, spawn=lambda c: None)
    slots.acquire(KEY)                       # a tag is in flight
    loop, fired = _build(store=store, slots=slots)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.PENDING


async def test_the_slot_is_released_even_when_fire_raises():
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    slots = ConversationSlots(maxlen=16, spawn=lambda c: None)
    metrics = Metrics()

    async def _boom(rows):
        raise RuntimeError("model exploded")

    loop, _ = _build(store=store, slots=slots, fire=_boom, metrics=metrics)

    await loop.tick()

    assert slots.held(KEY) is False
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_error"
    assert metrics.get("nora_reminders_fired_total", outcome="failed_error") == 1


async def test_the_liveness_gauge_climbs_when_nothing_is_ticking():
    """The gauge is computed at SCRAPE time, never published from inside the
    tick body. A loop that has STOPPED ticking must show a CLIMBING age; a
    gauge set inside the tick freezes at its last reading the moment the loop
    dies — the exact opposite of what a liveness signal is for (the presence
    collector publishes from inside its loop; copy the intent, not the code).

    Reading `metrics.get` is a scrape: `snapshot()` calls the registry's
    `collect()`, which evaluates the bound function. No tick happens between
    the two assertions below and the value still moves.
    """
    store = FakeReminderStore()
    metrics = Metrics()
    now = [T0]
    loop, _ = _build(store=store, metrics=metrics, clock=lambda: now[0])

    await loop.tick()
    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == 0.0

    now[0] = T0 + timedelta(seconds=180)     # the loop stops; nothing ticks
    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == 180.0


# NOTE: there is deliberately no `HeartbeatLoop.recover()`. Boot recovery is
# `ReminderStore.sweep_firing()`, called directly in composition.py and
# UNCONDITIONALLY — see Step 7. Putting it on the loop would make it dead code
# whenever [heartbeat] enabled = false, which is exactly when a stranded FIRING
# row most needs sweeping. Its behaviour is covered by
# tests/contract/test_reminder_store_contract.py::test_sweep_resolves_firing_rows_to_failed.
