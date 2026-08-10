"""The tick, with a fake clock and a stub `fire` — no model, no adapter, no
network. Every collaborator `HeartbeatLoop` has arrives through `__init__`,
including the clock, which is what makes the whole of §5 testable here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.heartbeat import (
    HEARTBEAT_NOT_RUNNING,
    HeartbeatLoop,
    publish_idle_gauge,
    recover,
)
from nora.domain.reminders import Reminder, ReminderState
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.testing.reminders import FakeReminderStore

T0 = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
KEY = ("signal", "conv-1")


class _RecordingAllowlist:
    """The `_Allowlist` shape, RECORDING what it was asked.

    A double that ignores its arguments and answers a constant cannot see the
    defect that matters here. `Policy.is_allowed` enforces the room gate AND
    the sender gate, and the two diverge exactly where it counts: in a room
    with an explicit `persons = [...]` list, `person_id=None` is refused; in a
    room with `persons = "*"` it is ALLOWED. So a regression that dropped the
    requester — passing `person_id=None`, or the conversation id in its place —
    would keep every assertion in this file green while quietly firing
    reminders on behalf of nobody in every wildcard room.

    `calls` is what makes that visible: the tests below assert the exact
    keywords, not merely that the predicate was consulted.
    """

    def __init__(self, *, allow: bool) -> None:
        self._allow = allow
        self.calls: list[dict[str, str | None]] = []

    def is_allowed(self, *, platform, conversation_id, person_id) -> bool:
        self.calls.append({
            "platform": platform,
            "conversation_id": conversation_id,
            "person_id": person_id,
        })   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
        return self._allow


class _StoreWithOneFailure(FakeReminderStore):
    """The real fake, with ONE injected failure on one method's Nth call.

    A subclass rather than a stub: every other call behaves exactly as the
    contract-tested store does, so what these tests observe is the LOOP's
    handling of a store failure and not an artefact of a fake that answers
    everything the same way (CLAUDE.md's "never write a test whose fake answers
    every request the same way").
    """

    def __init__(self, *, fail_on: str, nth: int) -> None:
        super().__init__()
        self._fail_on = fail_on
        self._nth = nth
        self._calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self._calls[name] = self._calls.get(name, 0) + 1
        if name == self._fail_on and self._calls[name] == self._nth:
            raise RuntimeError(f"store.{name} exploded")

    async def claim(self, reminder_id, *, now):
        self._count("claim")
        return await super().claim(reminder_id, now=now)

    async def mark(self, reminder_id, state, *, fired_at=None, outcome=None):
        self._count("mark")
        return await super().mark(
            reminder_id, state, fired_at=fired_at, outcome=outcome,
        )


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
        policy=policy if policy is not None else _RecordingAllowlist(allow=True),
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
    policy = _RecordingAllowlist(allow=True)
    loop, fired = _build(store=store, policy=policy)

    await loop.tick()

    assert [r.id for r in fired[0]] == [row.id]
    assert (await store.get(row.id)).state is ReminderState.DONE
    assert (await store.get(row.id)).outcome == "sent"
    # The allow path asks the SAME question the deny path does. Asserted on
    # both sides because a screen that dropped the requester would go on
    # answering True here — and would then be answering it about a room rather
    # than about a person.
    assert policy.calls == [{
        "platform": row.platform,
        "conversation_id": row.conversation_id,
        "person_id": row.requester_person_id,
    }]


async def test_a_row_not_yet_due_does_not_fire():
    store = FakeReminderStore()
    await store.create(_row(due_at=T0 + timedelta(minutes=1)))
    loop, fired = _build(store=store)

    await loop.tick()

    assert fired == []


async def test_rows_are_grouped_by_conversation_and_requester_one_turn_each():
    """§5.2: a turn has one conversation scope AND one requester.

    One conversation scope, because a turn has one Room, one policy decision and
    one Deliverer binding — sharing a turn across conversations leaks one room's
    reminder into the other.

    One REQUESTER, because §6.4 derives the whole turn from a single
    `requester_person_id`: the `tagger`, the `Room` scope it grants, the peer
    card, and the `sender_name` on the synthetic message. Two people's rows in
    one turn would answer person B's reminder inside a turn scoped to person A's
    identity and A's peer card — B's sentence spoken with A's memory and A's
    room. Two people due in the same group are therefore two turns, serialised
    by the slot.
    """
    store = FakeReminderStore()
    a1 = _row(conversation_id="conv-1", requester_person_id="miles")
    a2 = _row(conversation_id="conv-1", requester_person_id="miles")
    a3 = _row(conversation_id="conv-1", requester_person_id="tyler")
    b1 = _row(conversation_id="conv-2", requester_person_id="miles")
    for r in (a1, a2, a3, b1):
        await store.create(r)
    loop, fired = _build(store=store)

    await loop.tick()

    groups = {
        (rows[0].conversation_id, rows[0].requester_person_id): {r.id for r in rows}
        for rows in fired
    }
    assert groups == {
        ("conv-1", "miles"): {a1.id, a2.id},
        ("conv-1", "tyler"): {a3.id},
        ("conv-2", "miles"): {b1.id},
    }
    # Three turns, not two: the two requesters in conv-1 do not share one.
    assert len(fired) == 3
    for r in (a1, a2, a3, b1):
        assert (await store.get(r.id)).state is ReminderState.DONE


async def test_a_revoked_conversation_never_fires():
    """§5.1: policy.toml loads once at startup and a row may be 30 days old, so
    trust withdrawn in between must bite at fire time."""
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    metrics = Metrics()
    policy = _RecordingAllowlist(allow=False)
    loop, fired = _build(store=store, policy=policy, metrics=metrics)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_policy"
    assert metrics.get("nora_reminders_fired_total", outcome="failed_policy") == 1
    # The GRANT is (room, person), so the row's requester has to be in the
    # question. A screen that passed `person_id=None` would be refused here
    # anyway — and would silently ALLOW in a `persons = "*"` room, which is the
    # regression this assertion exists to catch.
    assert policy.calls == [{
        "platform": row.platform,
        "conversation_id": row.conversation_id,
        "person_id": row.requester_person_id,
    }]


async def test_a_held_slot_defers_to_the_next_tick():
    """§5.3: never shed, never wait. The row stays PENDING and the tick moves on
    — a reminder is not second-critical and the row persists.

    And it is COUNTED. A deferral is invisible to every other signal this
    feature has: nothing fired, so `nora_reminders_fired_total` is flat; the
    tick completed, so `nora_heartbeat_last_tick_age_seconds` drops back to
    zero. A group under continuous tag load can therefore starve its own
    reminders for hours with the whole dashboard reading healthy, which is what
    `nora_heartbeat_deferrals_total` is for.
    """
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    slots = ConversationSlots(maxlen=16, spawn=lambda c: None)
    slots.acquire(KEY)                       # a tag is in flight
    metrics = Metrics()
    loop, fired = _build(store=store, slots=slots, metrics=metrics)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.PENDING
    assert metrics.get("nora_heartbeat_deferrals_total") == 1
    # The two series that CANNOT see this, asserted so the counter's reason for
    # existing stays pinned rather than merely written down.
    assert metrics.get("nora_reminders_fired_total") == 0
    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == 0.0


async def test_a_tick_that_fires_normally_counts_no_deferral():
    """The control. Without it, a counter incremented unconditionally would
    satisfy the assertion above and make every fired reminder read as starved."""
    store = FakeReminderStore()
    await store.create(_row())
    metrics = Metrics()
    loop, fired = _build(store=store, metrics=metrics)

    await loop.tick()

    assert len(fired) == 1
    assert metrics.get("nora_heartbeat_deferrals_total") == 0


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


# --------------------------------------------------------------------------
# The state machine reads the OUTCOME it was handed (§8.1).
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["failed_timeout", "failed_error", "not_wired"])
async def test_an_outcome_that_is_not_sent_resolves_the_row_failed(outcome):
    """§8.1: the fire path answers `sent | failed_policy | failed_timeout |
    failed_error`, and §5's pseudocode records `done|failed` FROM it.

    Only an EXCEPTION used to produce FAILED, so every non-raising failure —
    a timed-out turn, a turn that reported its own error, and today's
    `not_wired` stub — was written to the row as `state=done` with a failure
    string in `outcome`. The row then reads "delivered" to `due()`, to
    `pending_for`, and to any operator who queries by state; only the free-text
    column disagrees. A reminder that never reached the group must not be
    recorded as one that did.
    """
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    metrics = Metrics()

    async def _fire(rows):
        return outcome

    loop, _ = _build(store=store, fire=_fire, metrics=metrics)

    await loop.tick()

    resolved = await store.get(row.id)
    assert resolved.state is ReminderState.FAILED
    assert resolved.outcome == outcome
    assert resolved.fired_at == T0
    assert metrics.get("nora_reminders_fired_total", outcome=outcome) == 1


async def test_the_sent_outcome_is_the_only_one_that_is_done():
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    loop, _ = _build(store=store)          # the default fire answers "sent"

    await loop.tick()

    assert (await store.get(row.id)).state is ReminderState.DONE


# --------------------------------------------------------------------------
# One bad reminder strands neither the others in its turn nor the conversations
# ordered after it — `tick()`'s docstring, made true.
# --------------------------------------------------------------------------


async def test_a_store_failure_while_claiming_strands_neither_row_nor_conversation():
    """A store that raises on the SECOND claim used to do two things at once:
    leave the first row FIRING with no counter and no terminal state, and take
    the whole tick down — so every conversation ordered after it was silently
    skipped until the next tick, and the one after that if the fault persisted.

    Both halves are asserted here: the already-claimed row is resolved, and the
    later conversation still fires.
    """
    store = _StoreWithOneFailure(fail_on="claim", nth=2)
    a1 = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=3))
    a2 = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=2))
    b1 = _row(conversation_id="conv-2", due_at=T0 - timedelta(minutes=1))
    for r in (a1, a2, b1):
        await store.create(r)
    slots = ConversationSlots(maxlen=16, spawn=lambda c: None)
    metrics = Metrics()
    loop, fired = _build(store=store, slots=slots, metrics=metrics)

    await loop.tick()          # must not raise

    # The row this tick had already moved to FIRING is resolved, not stranded.
    assert (await store.get(a1.id)).state is ReminderState.FAILED
    assert (await store.get(a1.id)).outcome == "failed_error"
    # The row the failing claim never moved is untouched: still PENDING, so the
    # next tick finds it again.
    assert (await store.get(a2.id)).state is ReminderState.PENDING
    # The conversation ordered AFTER the failure still had its turn.
    assert [r.id for r in fired[0]] == [b1.id]
    assert (await store.get(b1.id)).state is ReminderState.DONE
    assert slots.held(KEY) is False
    assert metrics.get("nora_errors_total", stage="heartbeat") == 1


async def test_a_store_failure_while_resolving_strands_neither_row_nor_conversation():
    """The same guarantee on the way OUT. The resolve loop was unguarded too, so
    a store that failed writing the FIRST row's terminal state skipped the
    second row's — leaving it FIRING (and therefore only recoverable at the next
    boot) — and killed the rest of the tick with it.
    """
    store = _StoreWithOneFailure(fail_on="mark", nth=1)   # claim() no longer marks
    a1 = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=3))
    a2 = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=2))
    b1 = _row(conversation_id="conv-2", due_at=T0 - timedelta(minutes=1))
    for r in (a1, a2, b1):
        await store.create(r)
    metrics = Metrics()
    loop, fired = _build(store=store, metrics=metrics)

    await loop.tick()          # must not raise

    # a1's terminal write is the one that failed: it stays FIRING and boot
    # recovery sweeps it (§8.2). That is the visible loss; the point of this
    # test is that it is the ONLY one.
    assert (await store.get(a1.id)).state is ReminderState.FIRING
    assert (await store.get(a2.id)).state is ReminderState.DONE
    assert (await store.get(b1.id)).state is ReminderState.DONE
    assert len(fired) == 2
    assert metrics.get("nora_errors_total", stage="heartbeat") == 1


# --------------------------------------------------------------------------
# PENDING -> FIRING is a compare-and-set, not a blind UPDATE.
# --------------------------------------------------------------------------


async def test_a_row_cancelled_during_an_earlier_turn_is_not_resurrected():
    """`due()` is a query, not a claim, so its batch is a snapshot of a past
    moment — and a turn awaits, so the world moves underneath it. A cancel
    landing while an earlier conversation's turn is in flight MUST win.

    It did not: both stores' `mark` updated by id alone with no state predicate,
    so the "already resolved" guard was dead code and the cancelled row went
    CANCELLED -> FIRING -> DONE and would have been spoken. The claim is a
    compare-and-set from PENDING now, which is the only thing that can decide
    this race in the store.
    """
    store = FakeReminderStore()
    first = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=2))
    cancelled = _row(conversation_id="conv-2", due_at=T0 - timedelta(minutes=1))
    await store.create(first)
    await store.create(cancelled)
    seen: list[list[Reminder]] = []

    async def _fire(rows):
        seen.append(list(rows))
        if rows[0].conversation_id == "conv-1":
            # A human cancels the other reminder while this turn is running.
            assert await store.cancel(cancelled.id, conversation_id="conv-2") is True
        return "sent"

    loop, _ = _build(store=store, fire=_fire)

    await loop.tick()

    assert [[r.id for r in rows] for rows in seen] == [[first.id]]
    resolved = await store.get(cancelled.id)
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome is None
    assert resolved.fired_at is None


# --------------------------------------------------------------------------
# Boot recovery, and the gauge that must not read healthy when nothing runs.
# --------------------------------------------------------------------------


async def test_boot_recovery_counts_every_row_it_sweeps():
    """`nora_reminders_fired_total`'s own spec says a reminder that vanishes
    must never vanish from the metrics too — and the sweep is the one path that
    makes reminders vanish without any tick having run. It logged a count and
    incremented nothing, so the rows a crash ate were invisible to every
    dashboard and every alert."""
    store = FakeReminderStore()
    stuck_a = _row(state=ReminderState.FIRING)
    stuck_b = _row(state=ReminderState.FIRING)
    ok = _row()
    for r in (stuck_a, stuck_b, ok):
        await store.create(r)
    metrics = Metrics()

    assert await recover(store=store, metrics=metrics, now=T0) == 2

    assert metrics.get("nora_reminders_fired_total", outcome="failed_error") == 2
    assert (await store.get(stuck_a.id)).state is ReminderState.FAILED
    # The sweep's own timestamp reaches the row, so «when was this handled?» has
    # an answer for the rows a crash ate — the ones it has an answer for
    # nowhere else.
    assert (await store.get(stuck_a.id)).fired_at == T0
    assert (await store.get(stuck_b.id)).fired_at == T0
    assert (await store.get(ok.id)).state is ReminderState.PENDING
    assert (await store.get(ok.id)).fired_at is None


async def test_recovery_that_sweeps_nothing_counts_nothing():
    store = FakeReminderStore()
    await store.create(_row())
    metrics = Metrics()

    assert await recover(store=store, metrics=metrics, now=T0) == 0

    assert metrics.get("nora_reminders_fired_total", outcome="failed_error") == 0


def test_the_liveness_gauge_reads_a_sentinel_when_no_loop_exists():
    """The gauge is bound in `HeartbeatLoop.__init__`, which never runs with the
    SHIPPED default (`[heartbeat] enabled = false`). An unbound gauge exports
    `0.0` — indistinguishable from "ticked a moment ago", which is precisely the
    reading this series exists to falsify. A negative sentinel can be no tick
    age at all, so "not running" is legible in one look and alertable in one
    expression."""
    metrics = Metrics()
    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == 0.0   # the blindness

    publish_idle_gauge(metrics)

    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == HEARTBEAT_NOT_RUNNING
    assert HEARTBEAT_NOT_RUNNING < 0


async def test_a_constructed_loop_takes_the_gauge_back_from_the_sentinel():
    """Order-independence: the sentinel is published unconditionally at startup
    and the loop, when it IS built, must own the series from then on."""
    store = FakeReminderStore()
    metrics = Metrics()
    publish_idle_gauge(metrics)

    now = [T0]
    loop, _ = _build(store=store, metrics=metrics, clock=lambda: now[0])
    await loop.tick()

    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == 0.0
    now[0] = T0 + timedelta(seconds=30)
    assert metrics.get("nora_heartbeat_last_tick_age_seconds") == 30.0


# NOTE: there is deliberately no `HeartbeatLoop.recover()`. Boot recovery is
# `recover()` — a module-level function over `ReminderStore.sweep_firing()`,
# called directly in composition.py and UNCONDITIONALLY. Putting it on the loop
# would make it dead code whenever [heartbeat] enabled = false, which is exactly
# when a stranded FIRING row most needs sweeping. The store half of it is
# tests/contract/test_reminder_store_contract.py::test_sweep_resolves_firing_rows_to_failed.
