"""The tick, with a fake clock and a stub `fire` — no model, no adapter, no
network. Every collaborator `HeartbeatLoop` has arrives through `__init__`,
including the clock, which is what makes the whole tick testable here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.heartbeat import (
    HEARTBEAT_NOT_RUNNING,
    HeartbeatLoop,
    _state_for,
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
    """A turn has one conversation scope AND one requester.

    One conversation scope, because a turn has one Room, one policy decision and
    one Deliverer binding — sharing a turn across conversations leaks one room's
    reminder into the other.

    One REQUESTER, because the whole turn is derived from a single
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
    """policy.toml loads once at startup and a row may be 30 days old, so
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
    """Never shed, never wait. The row stays PENDING and the tick moves on
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
# The state machine reads the OUTCOME it was handed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["failed_timeout", "failed_error", "not_wired"])
async def test_an_outcome_that_is_not_sent_resolves_the_row_failed(outcome):
    """The fire path answers `sent | failed_policy | failed_timeout |
    failed_error`, and the state machine records `done|failed` FROM it.

    A non-raising failure — a timed-out turn, a turn that reported its own error,
    and the `not_wired` stub — is recorded as FAILED, never as `state=done` with a
    failure string in `outcome`: the row would read "delivered" to `due()`, to
    `pending_for`, and to any operator who queries by state, while only the
    free-text column disagrees. A reminder that never reached the group must not be
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
    """A store that raises on the SECOND claim must not leave the first row
    FIRING with no counter and no terminal state, nor take the whole tick down so
    that every conversation ordered after it is skipped until the next tick.

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
    """The same guarantee on the way OUT: a store that fails writing the FIRST
    row's terminal state must not skip the second row's (leaving it FIRING, and
    therefore only recoverable at the next boot) or kill the rest of the tick.
    """
    store = _StoreWithOneFailure(fail_on="mark", nth=1)   # claim() is separate from mark()
    a1 = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=3))
    a2 = _row(conversation_id="conv-1", due_at=T0 - timedelta(minutes=2))
    b1 = _row(conversation_id="conv-2", due_at=T0 - timedelta(minutes=1))
    for r in (a1, a2, b1):
        await store.create(r)
    metrics = Metrics()
    loop, fired = _build(store=store, metrics=metrics)

    await loop.tick()          # must not raise

    # a1's terminal write is the one that failed: it stays FIRING and boot
    # recovery sweeps it. That is the visible loss; the point of this
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
    landing while an earlier conversation's turn is in flight MUST win: the claim
    is a compare-and-set from PENDING, which is the only thing that can decide
    this race in the store, so a cancelled row never goes CANCELLED -> FIRING ->
    DONE and is never spoken.
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
    makes reminders vanish without any tick having run. It must increment the
    counter, or the rows a crash ate are invisible to every dashboard and every
    alert."""
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


async def test_rows_of_two_kinds_same_requester_are_two_turns():
    """DEC-139: the grouping key includes `kind`. A plain reminder and a
    row of another kind, same requester, same conversation, same tick, are TWO
    turns — never a mixed batch whose kind is decided by row order.
    Accounting consequence: `nora_reminders_fired_total` — which increments PER
    ROW — counts 2 either way."""
    store = FakeReminderStore()
    plain = _row(text="買奶")
    other = _row(
        text="Event: Dinner", kind="other_kind",
    )
    await store.create(plain)
    await store.create(other)
    metrics = Metrics()
    loop, fired = _build(store=store, metrics=metrics)

    await loop.tick()

    assert len(fired) == 2
    kinds = {rows[0].kind for rows in fired}
    assert kinds == {"reminder", "other_kind"}
    # Both rows reached a terminal state — nothing was eaten by the split.
    assert (await store.get(plain.id)).state is ReminderState.DONE
    assert (await store.get(other.id)).state is ReminderState.DONE
    # Per-row counter: two reminders resolved, one per row, whatever the
    # turn split.
    assert metrics.get("nora_reminders_fired_total", outcome="sent") == 2


# ---- the proactive kind -------------


from nora.app.proactive_screen import ProactiveScreen  # noqa: E402
from nora.domain.notes import NoteSupersedeReason  # noqa: E402
from nora.testing import InMemoryNoteStore  # noqa: E402


class _RoomPolicy(_RecordingAllowlist):
    """`_RecordingAllowlist` plus the two reads `ProactiveScreen` makes: the
    per-room `proactive` flag and the room's unit."""

    def __init__(self, *, allow: bool, proactive: bool, unit: str = "friends") -> None:
        super().__init__(allow=allow)
        self._proactive = proactive
        self._unit = unit

    def proactive_allowed(self, *, platform: str, conversation_id: str) -> bool:
        return self._proactive

    def conversation(self, platform: str, conversation_id: str):
        from nora.policy import Conversation
        return Conversation(
            platform=platform, ids=frozenset({conversation_id}), persons=None,
            unit=self._unit, primary_id=conversation_id,
        )


async def _note_store(*, unit: str = "friends"):
    store = InMemoryNoteStore()
    note = await store.create(
        unit=unit, title="Dinner", description="venue open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="conv-1", body="Saturday; venue open.",
        established_by="tag:miles", now_ms=1_000, anchor_date=20260912,
    )
    return store, note


def _proactive_row(note, **kw) -> Reminder:
    base = {
        "requester_person_id": "nora", "kind": "proactive",
        "payload": {
            "note_id": note.id, "revision_id": note.head_revision_id,
            "note_title": note.title, "intent": "ask where they are",
        },
    }
    base.update(kw)
    return _row(**base)


def test_silent_is_done_not_failed():
    """The third outcome mapping. Only a proactive act turn can answer `silent`,
    and for it saying nothing is the healthy default — mapped to FAILED it
    reads as a delivery that never happened in every query, in the metric,
    and in the decision log the next decide turn learns from."""
    assert _state_for("silent") is ReminderState.DONE
    assert _state_for("sent") is ReminderState.DONE
    assert _state_for("failed_timeout") is ReminderState.FAILED


async def test_a_silent_proactive_turn_resolves_done_with_outcome_silent():
    store = FakeReminderStore()
    notes, note = await _note_store()
    row = _proactive_row(note)
    await store.create(row)
    metrics = Metrics()

    async def _fire(rows):
        return "silent"

    loop = HeartbeatLoop(
        store=store, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
        policy=_RoomPolicy(allow=False, proactive=True), clock=lambda: T0,
        metrics=metrics, tick_seconds=60.0, fire=_fire,
        proactive=ProactiveScreen(policy=_RoomPolicy(allow=False, proactive=True), notes=notes),
    )

    await loop.tick()

    resolved = await store.get(row.id)
    assert resolved.state is ReminderState.DONE
    assert resolved.outcome == "silent"
    assert metrics.get("nora_reminders_fired_total", outcome="silent") == 1


async def test_a_proactive_row_is_screened_on_the_flag_not_on_a_person():
    """The owner is the ai_peer id, which `is_allowed` can never admit
    — a policy that REFUSES every person still fires the row when the room's
    flag is on, and one that ALLOWS every person still refuses it when the
    flag is off. `is_allowed` is never even asked."""
    async def _run(*, allow: bool, proactive: bool):
        store = FakeReminderStore()
        notes, note = await _note_store()
        row = _proactive_row(note)
        await store.create(row)
        policy = _RoomPolicy(allow=allow, proactive=proactive)
        fired: list[list[Reminder]] = []

        async def _fire(rows):
            fired.append(list(rows))
            return "sent"

        loop = HeartbeatLoop(
            store=store, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
            policy=policy, clock=lambda: T0, metrics=Metrics(), tick_seconds=60.0,
            fire=_fire, proactive=ProactiveScreen(policy=policy, notes=notes),
        )
        await loop.tick()
        return fired, await store.get(row.id), policy.calls

    fired, resolved, calls = await _run(allow=False, proactive=True)
    assert len(fired) == 1 and resolved.state is ReminderState.DONE
    assert calls == []

    fired, resolved, calls = await _run(allow=True, proactive=False)
    assert fired == []
    assert resolved.state is ReminderState.FAILED
    assert resolved.outcome == "failed_policy"
    assert calls == []


async def test_a_proactive_row_with_no_screen_wired_fails_closed():
    store = FakeReminderStore()
    _notes, note = await _note_store()
    row = _proactive_row(note)
    await store.create(row)
    loop, fired = _build(store=store, policy=_RoomPolicy(allow=True, proactive=True))

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_policy"


async def test_a_tag_path_revision_since_the_link_cancels_the_row():
    """A human settling the thing at 11:00 means Nora does not raise
    it at 14:00. A TAG-PATH revision after the row's `revision_id` cancels
    it; a background revision (a debrief append, a curator write) does not,
    or a paraphrase would cancel a row on every tag."""
    store = FakeReminderStore()
    notes, note = await _note_store()
    row = _proactive_row(note)
    await store.create(row)
    policy = _RoomPolicy(allow=True, proactive=True)
    background = await notes.append_revision(
        unit="friends", note_id=note.id, body="Saturday; still open.",
        parent_revision_id=note.head_revision_id,
        established_by="dream:note-curation:friends:2", now_ms=2_000,
    )
    fired: list[list[Reminder]] = []

    async def _fire(rows):
        fired.append(list(rows))
        return "sent"

    def _loop():
        return HeartbeatLoop(
            store=store, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
            policy=policy, clock=lambda: T0, metrics=Metrics(), tick_seconds=60.0,
            fire=_fire, proactive=ProactiveScreen(policy=policy, notes=notes),
        )

    # Background move only: the row fires.
    await _loop().tick()
    assert len(fired) == 1

    # A tag-path move after the link: the next such row is cancelled.
    again = _proactive_row(note)
    await store.create(again)
    await notes.append_revision(
        unit="friends", note_id=note.id, body="Ada booked Sai Kung.",
        parent_revision_id=background.id, established_by="tag:ada", now_ms=3_000,
    )
    await _loop().tick()

    assert len(fired) == 1
    resolved = await store.get(again.id)
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome == "cancelled_note_moved"


async def test_a_retired_note_cancels_the_row():
    store = FakeReminderStore()
    notes, note = await _note_store()
    row = _proactive_row(note)
    await store.create(row)
    await notes.supersede(
        key="friends", target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=2_000,
    )
    policy = _RoomPolicy(allow=True, proactive=True)
    loop, fired = _build(store=store, policy=policy)
    loop._proactive = ProactiveScreen(policy=policy, notes=notes)

    await loop.tick()

    assert fired == []
    resolved = await store.get(row.id)
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome == "cancelled_note_retired"


async def test_a_pending_proactive_row_on_a_merged_note_follows_its_survivor():
    """A note retired `retired-merged` is not a question that
    closed — it was folded into another open note — so a pending row about
    it follows the survivor instead of being cancelled. It fires while the
    survivor stands untouched, and is cancelled `moved` once a person revises
    the survivor after the row was written.

    Falsification: treat every retirement as `cancelled_note_retired` (drop
    the `merged` branch) and the first tick cancels a row whose question is
    still open."""
    store = FakeReminderStore()
    notes, duplicate = await _note_store()
    survivor = await notes.create(
        unit="friends", title="Saturday dinner", description="venue open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="conv-1", body="Saturday; venue open.",
        established_by="tag:miles", now_ms=500, anchor_date=20260912,
    )
    row = _proactive_row(duplicate)
    await store.create(row)
    await notes.merge_into(
        unit="friends", survivor_id=survivor.id,
        survivor_head=survivor.head_revision_id, duplicate_id=duplicate.id,
        duplicate_head=duplicate.head_revision_id,
        body="Saturday; venue open.\n\n--- folded in from another note ---",
        anchor_date=20260912, revisit_at=2_000, reset_strikes=True,
        established_by="dream:note-merge:friends:2", now_ms=2_000,
    )
    policy = _RoomPolicy(allow=True, proactive=True)
    fired: list[list[Reminder]] = []

    async def _fire(rows):
        fired.append(list(rows))
        return "sent"

    def _loop():
        return HeartbeatLoop(
            store=store, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
            policy=policy, clock=lambda: T0, metrics=Metrics(), tick_seconds=60.0,
            fire=_fire, proactive=ProactiveScreen(policy=policy, notes=notes),
        )

    await _loop().tick()
    assert len(fired) == 1, "the fold is not a closing: the row fires"
    assert (await store.get(row.id)).state is ReminderState.DONE

    # A person revises the SURVIVOR after the next row was written.
    again = _proactive_row(duplicate)
    await store.create(again)
    head = (await notes.read(unit="friends", note_id=survivor.id))[1]
    await notes.append_revision(
        unit="friends", note_id=survivor.id, body="Ada booked Sai Kung.",
        parent_revision_id=head.id, established_by="tag:ada",
        now_ms=int(T0.timestamp() * 1000),
    )
    await _loop().tick()

    assert len(fired) == 1
    resolved = await store.get(again.id)
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome == "cancelled_note_moved"


async def test_a_row_on_a_merged_note_whose_survivor_closed_is_cancelled():
    """Following `replaced_by` must END somewhere open. A duplicate folded into
    a survivor the curator has since closed forwards to nothing, and the row
    is cancelled `retired` — not fired on a question that is over.

    Falsification: answer «fire» when `successor` is None and the row fires."""
    store = FakeReminderStore()
    notes, duplicate = await _note_store()
    survivor = await notes.create(
        unit="friends", title="Saturday dinner", description="venue open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="conv-1", body="Saturday; venue open.",
        established_by="tag:miles", now_ms=500, anchor_date=20260912,
    )
    row = _proactive_row(duplicate)
    await store.create(row)
    await notes.merge_into(
        unit="friends", survivor_id=survivor.id,
        survivor_head=survivor.head_revision_id, duplicate_id=duplicate.id,
        duplicate_head=duplicate.head_revision_id,
        body="Saturday; venue open.\n\n--- folded in from another note ---",
        anchor_date=20260912, revisit_at=2_000, reset_strikes=True,
        established_by="dream:note-merge:friends:2", now_ms=2_000,
    )
    await notes.supersede(
        key="friends", target=survivor.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=3_000,
    )
    policy = _RoomPolicy(allow=True, proactive=True)
    loop, fired = _build(store=store, policy=policy)
    loop._proactive = ProactiveScreen(policy=policy, notes=notes)

    await loop.tick()

    assert fired == []
    resolved = await store.get(row.id)
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome == "cancelled_note_retired"


async def test_a_linked_row_with_no_note_store_does_not_fire_unverified():
    """R-27 at the screen: a note store that is not wired is «I could not
    check», and a row whose premise cannot be checked does not fire —
    `failed_error`, never `cancelled` (a claim the note moved) and never
    admitted."""
    store = FakeReminderStore()
    _notes, note = await _note_store()
    row = _proactive_row(note)
    await store.create(row)
    policy = _RoomPolicy(allow=True, proactive=True)
    loop, fired = _build(store=store, policy=policy)
    loop._proactive = ProactiveScreen(policy=policy, notes=None)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_error"


async def test_a_proactive_row_with_no_note_is_screened_on_the_flag_alone():
    store = FakeReminderStore()
    row = _row(requester_person_id="nora", kind="proactive", payload={"intent": "hi"})
    await store.create(row)
    policy = _RoomPolicy(allow=False, proactive=True)
    loop, fired = _build(store=store, policy=policy)
    loop._proactive = ProactiveScreen(policy=policy, notes=None)

    await loop.tick()

    assert len(fired) == 1
    assert (await store.get(row.id)).state is ReminderState.DONE


async def test_a_note_the_rows_unit_cannot_reach_fails_closed():
    """`NoteStore.read` answers None for absent, for another
    unit and for a malformed id — «not reachable», which is not
    «retired». The policy's unit for the room moved between decide and
    fire; the row is FAILED as a wiring gap, never CANCELLED with an outcome
    claiming the note was retired, and never fired."""
    store = FakeReminderStore()
    notes, note = await _note_store(unit="friends")
    row = _proactive_row(note)
    await store.create(row)
    policy = _RoomPolicy(allow=True, proactive=True, unit="renamed")
    loop, fired = _build(store=store, policy=policy)
    loop._proactive = ProactiveScreen(policy=policy, notes=notes)

    await loop.tick()

    assert fired == []
    resolved = await store.get(row.id)
    assert resolved.state is ReminderState.FAILED
    assert resolved.outcome == "failed_error"


async def test_superseded_refuses_a_claimed_act_already_delivered():
    """The parent repro: a FIRING row whose (subject,action) already has a
    DONE+sent row must resolve, not fire — `open_proactive_subject` gated on
    open state made the delivered key unreadable."""
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from nora.app.heartbeat import HeartbeatLoop
    from nora.domain.reminders import Reminder, ReminderState
    from nora.testing.reminders import FakeReminderStore

    now = datetime.now(UTC)
    row = Reminder(
        id=uuid4(), kind="proactive", requester_person_id="synthetic-ai",
        platform="signal", conversation_id="generated-room",
        conversation_type="group", text="what time",
        created_at=now - timedelta(hours=2), due_at=now, fired_at=now,
        state=ReminderState.FIRING, outcome=None,
        payload={"subject_kind": "decision", "subject_id": "generated-decision",
                 "action": "ask-time", "plan_id": "p1"},
    )
    sent = replace(
        row, id=uuid4(), state=ReminderState.DONE, outcome="sent",
        fired_at=now - timedelta(minutes=30), payload={**row.payload, "plan_id": "p2"},
    )
    store = FakeReminderStore()
    await store.create(sent)
    loop = SimpleNamespace(_store=store)
    refusal = await HeartbeatLoop._superseded(loop, row)
    assert refusal == (ReminderState.FAILED, "failed_superseded")


async def test_superseded_lets_a_same_plan_row_fire():
    """A deliberate multi-room act (same `plan_id`) is not superseded by its
    sibling's delivery — the plan is one producer intent."""
    from dataclasses import replace
    from types import SimpleNamespace
    from uuid import uuid4

    from nora.app.heartbeat import HeartbeatLoop
    from nora.domain.reminders import Reminder, ReminderState
    from nora.testing.reminders import FakeReminderStore

    now = datetime.now(UTC)
    row = Reminder(
        id=uuid4(), kind="proactive", requester_person_id="synthetic-ai",
        platform="signal", conversation_id="generated-room",
        conversation_type="group", text="what time",
        created_at=now - timedelta(hours=2), due_at=now, fired_at=now,
        state=ReminderState.FIRING, outcome=None,
        payload={"subject_kind": "decision", "subject_id": "generated-decision",
                 "action": "ask-time", "plan_id": "p1"},
    )
    sibling = replace(
        row, id=uuid4(), conversation_id="other-room",
        state=ReminderState.DONE, outcome="sent",
        fired_at=now - timedelta(minutes=30),
    )
    store = FakeReminderStore()
    await store.create(sibling)
    loop = SimpleNamespace(_store=store)
    assert await HeartbeatLoop._superseded(loop, row) is None
