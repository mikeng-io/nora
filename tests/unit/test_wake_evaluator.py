"""The wake policy (`nora.domain.stimuli.decide`) and the SHADOW evaluator
(`nora.app.wake.WakeEvaluator`) — design/cognition-loop/20-observe.md §3,
migration step 1.

Pinned here: each trigger of the wake table, in the table's order; a `waits`
stimulus never wakes a unit alone; watermark consumption (a would-wake consumes
through the id it read, later stimuli stay pending, and one due later stays
pending past it); single-flight; the cooldown and the one strong cue that may
cut it; the cross-unit concurrency bound; the daily floors; and that the
evaluator writes a would-wake and NOTHING ELSE — the positive control of «it
fires nothing».

On `InMemoryStimulusStore`, which `tests/contract/test_stimulus_store_contract.py`
binds to the SQL store's consumption rule.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from nora.app.wake import Floor, WakeEvaluator
from nora.domain.stimuli import NewStimulus, WakePolicy
from nora.obs.metrics import Metrics
from nora.testing.stimuli import InMemoryStimulusStore

HK = ZoneInfo("Asia/Hong_Kong")
# 2026-09-28 15:00 HK — no floor within the default grace.
T = int(dt.datetime(2026, 9, 28, 15, 0, tzinfo=HK).timestamp() * 1000)
S = 1000
POLICY = WakePolicy(
    quiet_seconds=1800, cue_quiet_seconds=120, rollup_delay_seconds=60, batch_max=5,
    max_age_seconds=3600, cooldown_seconds=300, max_concurrent_sessions=2,
    session_seconds=60,
)


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000

    def advance(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


def _setup(*, units=("nora",), policy=POLICY, floors=(), start=T, settle=0.0, on_wake=None):
    store = InMemoryStimulusStore()
    clock = Clock(start)
    metrics = Metrics()
    ev = WakeEvaluator(
        store=store, units=lambda: units, policy=policy, metrics=metrics,
        floors=floors, tz=HK, clock=clock, settle_seconds=settle, on_wake=on_wake,
    )
    return ev, store, clock, metrics


async def _say(store, clock, *, unit="nora", cues=(), waits=False, kind="message",
               due_ms=None, ref=None):
    return await store.append(stimulus=NewStimulus(
        unit=unit, kind=kind, item=kind if kind != "message" else "message",
        ref=ref or f"messages:{len(store.stimuli) + 1}", at_ms=clock.ms,
        conversation_id="g1" if kind == "message" else None,
        cues=tuple(cues), waits=waits, due_ms=due_ms,
    ), recorded_at_ms=clock.ms)


# ── the triggers ─────────────────────────────────────────────────────────────


async def test_nothing_pending_records_nothing():
    ev, store, _clock, _ = _setup()
    assert await ev.tick() == []
    assert store.wakes == []


def test_wake_policy_bare_default_quiet_seconds_is_1800():
    """The dataclass default, unmediated by `POLICY` above or
    `composition.py` (which always passes `cfg.cognition.quiet_seconds`
    explicitly and so never exercises this default at all)."""
    assert WakePolicy().quiet_seconds == 1800.0


async def test_settled_fires_after_quiet_seconds_and_not_before():
    ev, store, clock, metrics = _setup()
    await _say(store, clock)
    clock.advance(1799)
    assert await ev.tick() == []
    clock.advance(1)
    assert len(await ev.tick()) == 1
    assert store.wakes[0].reason == "settled" and store.wakes[0].mode == "shadow"
    assert metrics.get("nora_cognition_wake_total", reason="settled", mode="shadow") == 1


async def test_a_strong_cue_fires_after_cue_quiet_seconds():
    ev, store, clock, _ = _setup()
    await _say(store, clock, cues=("datetime",))
    clock.advance(119)
    assert await ev.tick() == []
    clock.advance(1)
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["strong_cue"]


async def test_a_weak_cue_waits_for_a_settled_room():
    ev, store, clock, _ = _setup()
    await _say(store, clock, cues=("amount",))
    clock.advance(300)
    assert await ev.tick() == []


async def test_batch_fires_on_batch_max_pending_in_a_live_room():
    ev, store, clock, _ = _setup()
    for _ in range(5):
        await _say(store, clock)
        clock.advance(10)
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["batch"]
    assert store.wakes[0].stimulus_count == 5


async def test_max_age_fires_in_a_room_that_never_goes_quiet():
    ev, store, clock, _ = _setup(policy=WakePolicy(quiet_seconds=10**6, batch_max=10**6,
                                                  cue_quiet_seconds=10**6))
    await _say(store, clock)
    for _ in range(12):                  # a message every 5 minutes for an hour
        clock.advance(300)
        await _say(store, clock)
        await ev.tick()
    assert [w.reason for w in store.wakes] == ["max_age"]


async def test_a_rollup_stimulus_fires_after_its_delay_generically():
    """No producer until step 2; the evaluator handles the kind already."""
    ev, store, clock, _ = _setup()
    await _say(store, clock)                                  # the room is live
    await _say(store, clock, kind="rollup", ref="rollup:colocation:1")
    clock.advance(59)
    assert await ev.tick() == []
    clock.advance(1)
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["rollup"]


async def test_a_self_stimulus_is_pending_only_from_its_due_time():
    """No producer until step 3's `wake_at`; handled generically, and it
    does not hold the queue open before it is due (20 §3)."""
    ev, store, clock, _ = _setup()
    await _say(store, clock, kind="self", ref="wake:1", due_ms=clock.ms + 3600 * S)
    clock.advance(3599)
    assert await ev.tick() == []
    clock.advance(1)
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["self"]


async def test_a_reply_to_nora_never_wakes_a_unit_by_itself():
    ev, store, clock, _ = _setup()
    await _say(store, clock, waits=True, cues=("datetime",))
    clock.advance(4000)                       # past quiet, cue-quiet and max age
    assert await ev.tick() == []
    # ...and rides the next wake a person's item starts.
    await _say(store, clock)
    clock.advance(1800)
    await ev.tick()
    assert len(store.wakes) == 1
    assert (store.wakes[0].stimulus_count, store.wakes[0].eligible_count) == (2, 1)
    assert store.wakes[0].detail["waits"] == 1


# ── the watermark ────────────────────────────────────────────────────────────


async def test_a_would_wake_consumes_through_the_id_it_read():
    ev, store, clock, _ = _setup()
    a = await _say(store, clock)
    clock.advance(1800)
    await ev.tick()
    w = store.wakes[0]
    assert (w.from_stimulus_id, w.through_stimulus_id) == (0, a)
    clock.advance(400)                        # past session + cooldown
    b = await _say(store, clock)
    clock.advance(1800)
    await ev.tick()
    assert (store.wakes[1].from_stimulus_id, store.wakes[1].through_stimulus_id) == (a, b)
    assert store.wakes[1].stimulus_count == 1   # `a` was consumed, not re-read


async def test_the_watermark_survives_a_restart():
    """A fresh evaluator over the same rows is the same evaluator."""
    ev, store, clock, metrics = _setup()
    await _say(store, clock)
    clock.advance(1800)
    await ev.tick()
    fresh = WakeEvaluator(store=store, units=lambda: ("nora",), policy=POLICY,
                          metrics=metrics, tz=HK, clock=clock, settle_seconds=0)
    clock.advance(1000)
    assert await fresh.tick() == []
    assert len(store.wakes) == 1


# ── the brakes ───────────────────────────────────────────────────────────────


async def test_single_flight_holds_a_unit_while_its_would_session_runs():
    ev, store, clock, metrics = _setup(policy=WakePolicy(
        quiet_seconds=600, batch_max=2, cooldown_seconds=0, session_seconds=60))
    await _say(store, clock)
    await _say(store, clock)
    await ev.tick()                                   # batch
    await _say(store, clock)
    await _say(store, clock)
    clock.advance(30)
    assert await ev.tick() == []                      # still «running»
    assert metrics.get("nora_cognition_wake_deferred_total", brake="single_flight") == 1
    clock.advance(30)
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["batch", "batch"]


async def test_the_cooldown_holds_a_settled_unit():
    ev, store, clock, metrics = _setup(policy=WakePolicy(
        quiet_seconds=0, cooldown_seconds=300, session_seconds=60))
    await _say(store, clock)
    await ev.tick()
    clock.advance(61)
    await _say(store, clock)
    assert await ev.tick() == []
    assert metrics.get("nora_cognition_wake_deferred_total", brake="cooldown") == 1
    clock.advance(300)
    await ev.tick()
    assert len(store.wakes) == 2


async def test_one_strong_cue_may_cut_the_cooldown_once():
    ev, store, clock, _ = _setup(policy=WakePolicy(
        quiet_seconds=0, cue_quiet_seconds=0, cooldown_seconds=300, session_seconds=60))
    await _say(store, clock)
    await ev.tick()                                   # settled
    clock.advance(61)
    await _say(store, clock, cues=("keep",))
    await ev.tick()                                   # cuts the cooldown
    clock.advance(61)
    await _say(store, clock, cues=("datetime",))
    assert await ev.tick() == []                      # cannot cut the next one
    assert [(w.reason, w.cut_cooldown) for w in store.wakes] == [
        ("settled", False), ("strong_cue", True),
    ]


async def test_at_most_max_concurrent_sessions_across_units():
    ev, store, clock, metrics = _setup(units=("a", "b", "c"), policy=WakePolicy(
        quiet_seconds=0, max_concurrent_sessions=2, session_seconds=60))
    for unit in ("a", "b", "c"):
        await _say(store, clock, unit=unit)
    await ev.tick()
    assert sorted(w.unit for w in store.wakes) == ["a", "b"]
    assert metrics.get("nora_cognition_wake_deferred_total", brake="concurrency") == 1
    clock.advance(60)
    await ev.tick()
    assert sorted(w.unit for w in store.wakes) == ["a", "b", "c"]


# ── the floors ───────────────────────────────────────────────────────────────


async def test_the_daily_floors_are_raised_once_and_wake_as_time():
    at_seven = int(dt.datetime(2026, 9, 28, 7, 0, tzinfo=HK).timestamp() * 1000)
    ev, store, clock, _ = _setup(floors=(Floor.parse("07:00"), Floor.parse("00:00")),
                                 start=at_seven - 30 * S)
    assert await ev.tick() == []                      # 06:59:30: not yet
    clock.advance(30)
    await ev.tick()
    clock.advance(30)
    await ev.tick()
    floors = [s for s in store.stimuli if s.kind == "time"]
    assert [s.ref for s in floors] == ["floor:07:00@2026-09-28"]
    assert [w.reason for w in store.wakes] == ["time"]


async def test_a_floor_past_its_grace_is_not_raised():
    """A deploy at 15:00 does not raise that morning's 07:00 as due now."""
    ev, store, _, _ = _setup(floors=(Floor.parse("07:00"),))
    await ev.tick()
    assert store.stimuli == [] and store.wakes == []


# ── shadow fires nothing ─────────────────────────────────────────────────────


async def test_a_shadow_tick_writes_a_would_wake_and_nothing_else():
    """The positive control of «it fires nothing»: the ONE row a would-wake
    produces is a shadow wake row; the stimulus table is untouched (append-only,
    nothing marked consumed) and nothing else is written."""
    ev, store, clock, _ = _setup()
    await _say(store, clock, cues=("datetime",))
    before = list(store.stimuli)
    clock.advance(600)
    written = await ev.tick()
    assert len(written) == 1 and len(store.wakes) == 1
    assert store.wakes[0].mode == "shadow"
    assert store.stimuli == before



# ── the two ways a watermark could lose a stimulus ─────────────────────────────


async def test_a_due_later_wake_does_not_resurrect_stimuli_already_consumed():
    """A `self` stimulus appended first (id 1) and due later is consumed by a
    wake AFTER ids 2-3 were consumed; its wake must not set the watermark
    back to id 1 and re-open them."""
    ev, store, clock, _ = _setup(policy=WakePolicy(quiet_seconds=0, cooldown_seconds=0,
                                                  session_seconds=0))
    await _say(store, clock, kind="self", ref="wake:1", due_ms=clock.ms + 3600 * S)
    await _say(store, clock)
    await _say(store, clock)
    await ev.tick()
    assert [(w.reason, w.through_stimulus_id, w.stimulus_count) for w in store.wakes] == [
        ("settled", 3, 2)]
    clock.advance(3600)
    await ev.tick()
    assert store.wakes[-1].reason == "self"
    assert store.wakes[-1].through_stimulus_id == 3
    clock.advance(600)
    assert await ev.tick() == [], "ids 2-3 came back as pending"


async def test_an_id_committed_after_a_higher_one_is_not_consumed_unseen():
    """Concurrent ingests commit out of id order. With no settle margin a
    read that saw id 2 before id 1 committed consumes id 1 unseen; with the
    margin the read waits until both are in."""
    lost_ev, lost, clock, _ = _setup(policy=WakePolicy(quiet_seconds=0))
    first = await _say(lost, clock)
    lost.uncommitted.add(first)
    await _say(lost, clock)
    clock.advance(2)
    await lost_ev.tick()                         # sees only id 2
    lost.uncommitted.clear()                     # id 1 commits
    assert lost.wakes[0].stimulus_count == 1
    view = await lost.view(unit="nora", mode="shadow", now_ms=clock.ms + 10**6, limit=100)
    assert first not in [s.id for s in view.pending], "control: the race loses id 1"

    ev, store, clock, _ = _setup(policy=WakePolicy(quiet_seconds=0), settle=5)
    first = await _say(store, clock)
    store.uncommitted.add(first)
    await _say(store, clock)
    clock.advance(2)
    assert await ev.tick() == []                  # both inside the margin
    clock.advance(1)
    store.uncommitted.clear()                     # id 1 commits within the margin
    clock.advance(4)
    await ev.tick()
    assert [w.stimulus_count for w in store.wakes] == [2]
    assert store.wakes[0].at_ms == clock.ms - 5 * S   # the cutoff is the time bound


# ── the on_wake hook (migration step 3a-ii) ─────────────────────────────────


async def test_on_wake_is_never_called_when_none_the_off_wiring():
    """`session_mode = "off"` is exactly "the composition root never passes
    `on_wake`" — this is the evaluator-level half of that property: with no
    hook, a tick behaves byte-identically to before the parameter existed."""
    ev, store, clock, _ = _setup(policy=WakePolicy(quiet_seconds=0))
    await _say(store, clock)
    clock.advance(1)
    assert await ev.tick() != []
    await ev.drain()  # no-op: nothing was ever scheduled


async def test_on_wake_is_called_once_per_would_wake_with_the_wake_id():
    calls = []

    async def hook(*, unit, view, wake_id, now_ms):
        calls.append((unit, wake_id))

    ev, store, clock, _ = _setup(policy=WakePolicy(quiet_seconds=0), on_wake=hook)
    await _say(store, clock)
    clock.advance(1)
    wake_ids = await ev.tick()
    await ev.drain()
    assert len(wake_ids) == 1
    assert calls == [("nora", wake_ids[0])]


async def test_on_wake_a_failing_hook_is_caught_and_counted_never_taking_the_tick_down():
    async def hook(*, unit, view, wake_id, now_ms):
        raise RuntimeError("a session blew up")

    ev, store, clock, metrics = _setup(policy=WakePolicy(quiet_seconds=0), on_wake=hook)
    await _say(store, clock)
    clock.advance(1)
    wake_ids = await ev.tick()  # the tick itself must not raise
    await ev.drain()
    assert wake_ids != []
    assert metrics.get("nora_errors_total", stage="cognition_session") == 1


async def test_on_wake_a_sync_hook_with_no_awaitable_return_is_a_no_op():
    calls = []

    def hook(*, unit, view, wake_id, now_ms):
        calls.append(wake_id)
        # returns None, not a coroutine — must not be scheduled or awaited

    ev, store, clock, metrics = _setup(policy=WakePolicy(quiet_seconds=0), on_wake=hook)
    await _say(store, clock)
    clock.advance(1)
    await ev.tick()
    await ev.drain()
    assert len(calls) == 1
    assert ev._background == set()
    # A hook that returns a coroutine gets an error path if it raises
    # (the test above); a plain `None` return scheduled and awaited would
    # raise `TypeError: object NoneType can't be used in 'await' expression`
    # inside the background task, caught the same way — so the only way to
    # tell "never scheduled" apart from "scheduled and its error was caught"
    # is that NEITHER a background task NOR an error is left behind.
    assert metrics.get("nora_errors_total", stage="cognition_session") == 0


# ── D2: a rollup, time or self wake claims only its own kind (DEC-245) ────────


async def _pending(store, unit="nora", *, now_ms=None, clock=None):
    view = await store.view(unit=unit, mode="shadow", now_ms=now_ms or clock.ms, limit=1000)
    return [(s.id, s.kind) for s in view.pending]


async def test_a_rollup_wake_takes_the_rollup_and_leaves_an_unsettled_room_pending():
    """Before: the rollup wake consumed the room's conversation too, unseen by
    anything that judges a room. Now the messages wait for their own settling."""
    ev, store, clock, _ = _setup()
    m = await _say(store, clock)                                   # the room is live
    r = await _say(store, clock, kind="rollup", ref="rollup:colocation:1")
    clock.advance(60)
    await ev.tick()
    (wake,) = store.wakes
    assert wake.reason == "rollup"
    assert (wake.stimulus_count, wake.eligible_count) == (1, 1)
    assert wake.through_stimulus_id == r and wake.unclaimed_ids == (m,)
    assert await _pending(store, clock=clock) == [(m, "message")]


async def test_the_messages_a_rollup_wake_left_are_taken_once_by_their_own_settled_wake():
    ev, store, clock, _ = _setup()
    m = await _say(store, clock)
    await _say(store, clock, kind="rollup", ref="rollup:colocation:1")
    clock.advance(60)
    await ev.tick()
    clock.advance(1800)                       # the room settles, past the cooldown
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["rollup", "settled"]
    assert store.wakes[1].stimulus_count == 1
    assert store.wakes[1].through_stimulus_id == max(m, 2)
    assert store.wakes[1].unclaimed_ids == ()
    clock.advance(4000)
    assert await ev.tick() == []                              # and never again
    assert await _pending(store, clock=clock) == []


async def test_a_settled_wake_still_claims_everything_pending():
    """The narrowing is for rollup, time and self only: a settled room's wake
    takes the rollup row that arrived beside its messages (one agenda)."""
    ev, store, clock, _ = _setup(policy=WakePolicy(
        quiet_seconds=1800, cue_quiet_seconds=120, rollup_delay_seconds=10**6, batch_max=50,
        max_age_seconds=10**6, cooldown_seconds=300, session_seconds=60,
    ))
    await _say(store, clock)
    await _say(store, clock, kind="rollup", ref="rollup:colocation:1")
    clock.advance(1800)
    await ev.tick()
    (wake,) = store.wakes
    assert wake.reason == "settled" and wake.stimulus_count == 2 and wake.unclaimed_ids == ()
    assert await _pending(store, clock=clock) == []


async def test_a_self_wake_leaves_a_message_pending_and_a_time_wake_leaves_a_self_wake():
    ev, store, clock, _ = _setup()
    await _say(store, clock, kind="self", ref="wake:1", due_ms=clock.ms + 100 * S)
    m = await _say(store, clock)
    clock.advance(100)
    await ev.tick()
    assert [w.reason for w in store.wakes] == ["self"]
    assert await _pending(store, clock=clock) == [(m, "message")]


async def test_the_hook_is_handed_only_what_the_wake_claimed():
    seen = []

    async def hook(*, unit, view, wake_id, now_ms):
        seen.append([(s.id, s.kind) for s in view.pending])

    ev, store, clock, _ = _setup(on_wake=hook)
    await _say(store, clock)
    await _say(store, clock, kind="rollup", ref="rollup:colocation:1")
    clock.advance(60)
    await ev.tick()
    await ev.drain()
    assert seen == [[(2, "rollup")]]


# ── M8: no wake on a time floor until its skill exists (DEC-245) ──────────────


async def _noop_hook(*, unit, view, wake_id, now_ms):
    return None


def _floor_setup(*, floors_ready, units=("nora",), session_units=None):
    store = InMemoryStimulusStore()
    clock = Clock(int(dt.datetime(2026, 9, 28, 7, 5, tzinfo=HK).timestamp() * 1000))
    ev = WakeEvaluator(
        store=store, units=lambda: units, policy=POLICY, metrics=Metrics(),
        floors=(Floor(7, 0),), tz=HK, clock=clock, settle_seconds=0,
        on_wake=_noop_hook, session_units=session_units, floors_ready=floors_ready,
    )
    return ev, store, clock


async def test_a_unit_whose_wakes_start_sessions_raises_no_floor_while_no_skill_ships():
    ev, store, _clock = _floor_setup(floors_ready=False)
    assert await ev.tick() == []
    assert store.stimuli == [] and store.wakes == []


async def test_a_ready_floor_is_raised_and_wakes_as_time_for_a_session_unit():
    ev, store, _clock = _floor_setup(floors_ready=True)
    await ev.tick()
    assert [s.kind for s in store.stimuli] == ["time"]
    assert [w.reason for w in store.wakes] == ["time"]


async def test_a_unit_that_only_records_would_wakes_still_raises_its_floor():
    """Sizing the floors from a shadow week is what they are for: only a unit
    that would START a session is held back."""
    ev, store, _clock = _floor_setup(
        floors_ready=False, units=("a", "b"), session_units=lambda: {"a"},
    )
    await ev.tick()
    assert {s.unit for s in store.stimuli} == {"b"}


async def test_a_time_stimulus_already_pending_wakes_no_session_while_no_skill_ships():
    """M8, the other half (strongest-tier review of #646): not RAISING a floor
    is not enough. A floor raised before the gate (or by the previous deploy) is
    already pending, and it woke a session all the same. It stays pending, is
    never claimed and never consumed, and wakes the moment the kind is ready."""
    seen: list[list[str]] = []

    async def hook(*, unit, view, wake_id, now_ms):
        seen.append([s.kind for s in view.pending])

    ev, store, clock = _floor_setup(floors_ready=False)
    ev._on_wake = hook
    await store.append(stimulus=NewStimulus(
        unit="nora", kind="time", item="floor", ref="floor:07:00@2026-09-28",
        at_ms=clock.ms - 60_000, due_ms=clock.ms - 60_000,
    ), recorded_at_ms=clock.ms)
    assert await ev.tick() == []
    assert store.wakes == [] and seen == []
    view = await store.view(unit="nora", mode="shadow", now_ms=clock.ms, limit=10)
    assert [s.kind for s in view.pending] == ["time"]          # kept, not consumed
    ev._floors_ready = True
    assert len(await ev.tick()) == 1
    await ev.drain()
    assert seen == [["time"]]


async def test_a_pending_time_stimulus_does_not_ride_a_room_wake_when_its_kind_is_inert():
    seen: list[list[str]] = []

    async def hook(*, unit, view, wake_id, now_ms):
        seen.append([s.kind for s in view.pending])

    ev, store, clock = _floor_setup(floors_ready=False)
    ev._on_wake = hook
    await store.append(stimulus=NewStimulus(
        unit="nora", kind="time", item="floor", ref="floor:07:00@2026-09-28",
        at_ms=clock.ms - 60_000, due_ms=clock.ms - 60_000,
    ), recorded_at_ms=clock.ms)
    await store.append(stimulus=NewStimulus(
        unit="nora", kind="message", item="message", ref="messages:1", at_ms=clock.ms,
        platform="signal", conversation_id="g1",
    ), recorded_at_ms=clock.ms)
    clock.advance(1900)                      # past `quiet_seconds`: the room has settled
    assert len(await ev.tick()) == 1
    await ev.drain()
    assert seen == [["message"]]
    view = await store.view(unit="nora", mode="shadow", now_ms=clock.ms, limit=10)
    assert [s.kind for s in view.pending] == ["time"]          # listed unclaimed, still pending


AGE = "nora_stimulus_pending_age_seconds"


async def _time_floor(store, clock, *, hours_old):
    at = clock.ms - hours_old * 3_600_000
    await store.append(stimulus=NewStimulus(
        unit="nora", kind="time", item="floor", ref="floor:07:00@2026-09-28",
        at_ms=at, due_ms=at,
    ), recorded_at_ms=clock.ms)


async def test_an_inert_time_stimulus_does_not_age_the_backlog_gauge():
    """an operator reads this gauge as «the oldest stimulus that COULD wake the
    unit». A `time` floor while no skill ships for it never can (`inert_kinds`), so
    five hours of it is not a stuck unit: the gauge counted it and the alert would
    have fired on deploy and stayed (review of #714, probe: one tick set 18000)."""
    ev, store, clock = _floor_setup(floors_ready=False)
    await _time_floor(store, clock, hours_old=5)
    assert await ev.tick() == []                          # nothing woke: it IS inert
    assert ev._metrics.get(AGE, unit="nora") == 0.0


async def test_the_backlog_gauge_still_counts_a_time_stimulus_once_its_kind_is_ready():
    """The control: the same floor, the kind ready, IS the oldest thing that could wake
    the unit, so the gauge carries its age. Only the inert kind is skipped."""
    ev, store, clock = _floor_setup(floors_ready=True)
    await _time_floor(store, clock, hours_old=5)
    await ev.tick()
    assert ev._metrics.get(AGE, unit="nora") == 5 * 3600


async def test_the_backlog_gauge_skips_the_inert_kind_and_keeps_the_next_oldest():
    ev, store, clock = _floor_setup(floors_ready=False)
    await _time_floor(store, clock, hours_old=5)
    await store.append(stimulus=NewStimulus(
        unit="nora", kind="message", item="message", ref="messages:1",
        at_ms=clock.ms - 3_600_000, platform="signal", conversation_id="g1",
    ), recorded_at_ms=clock.ms - 3_600_000)
    await ev.tick()
    assert ev._metrics.get(AGE, unit="nora") == 3600


def test_the_inert_kinds_are_time_only_while_tracked_and_no_skill_ships():
    from nora.app.cognition_wiring import AGENDA_SKILLS, floors_ready
    from nora.domain.stimuli import inert_kinds_for

    assert inert_kinds_for(tracked=True, floors_ready=False) == {"time"}
    assert inert_kinds_for(tracked=True, floors_ready=True) == frozenset()
    assert inert_kinds_for(tracked=False, floors_ready=False) == frozenset()
    assert "time" not in AGENDA_SKILLS and floors_ready() is False   # production: `time` is inert


def test_an_unclaimed_id_is_not_consumed_by_the_range_that_covers_it():
    from nora.domain.stimuli import Stimulus, Watermark

    s = Stimulus(id=3, unit="nora", kind="message", item="message", ref="messages:3",
                 at_ms=10, recorded_at_ms=10)
    assert Watermark(5, 100).consumed(s) is True
    assert Watermark(5, 100, frozenset({3})).consumed(s) is False


def test_a_stimulus_due_after_the_watermarks_time_is_not_consumed_by_the_id_range():
    """A `self` stimulus appended early (a low id) and due at 19:40 is under a
    15:00 watermark's id but not yet pending at its time, so the wake that set
    the watermark did not take it. `Watermark.consumed` is what strikes pending
    stimuli after repeated failed agendas; reading only the id would strike one
    nobody has seen. The control is the same stimulus once the watermark's time
    has reached its due time."""
    from nora.domain.stimuli import Stimulus, Watermark

    due_later = Stimulus(id=3, unit="nora", kind="self", item="self", ref="wake:1",
                         at_ms=10, recorded_at_ms=10, due_ms=200)
    assert Watermark(5, 100).consumed(due_later) is False
    assert Watermark(5, 300).consumed(due_later) is True
