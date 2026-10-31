"""`ParticipationTrigger` — one evaluation per quiescence-closed window.

A candidate window OPENS on an opted-in
room's untagged message and CLOSES when the room goes quiet for
`settle_seconds`; exactly one participation evaluation runs per closed window;
the slot is try_acquire-and-skip; the cooldown caps SENDS, never evaluations.
Every test drives `close_due_windows` directly against an injected clock —
never a sleep — the same discipline `Debounce`'s own tests keep.
"""

from __future__ import annotations

import contextlib
import inspect
import time

import pytest

from nora.app.fire import SENT, SILENT
from nora.app.participation import ParticipationTrigger
from nora.domain.models import NormalizedMessage
from nora.domain.participation import ParticipationCursor
from nora.engagement.slots import ConversationSlots

SETTLE = 10.0
COOLDOWN = 600.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _Speak:
    """Stands in for `FirePath.speak`: records `(occasion, send_permitted)`."""

    def __init__(self, outcome: str = SILENT) -> None:
        self.calls: list = []
        self.outcome = outcome

    async def __call__(
        self, occasion, *, send_permitted: bool = True,
        gate_verdict: str | None = None, gate_score: float | None = None,
    ) -> str:
        self.calls.append((occasion, send_permitted, gate_verdict, gate_score))
        return self.outcome


def _msg(conv: str = "g1", body: str = "hi", ctype: str = "group") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type=ctype,
        sender_id="+852111", sender_name="Miles", timestamp=1, body=body, raw={},
    )


def _trigger(
    speak: _Speak, clock: _Clock, *, cooldown: float = COOLDOWN,
) -> ParticipationTrigger:
    return ParticipationTrigger(
        speak=speak,
        slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=cooldown, clock=clock,
    )


async def test_a_window_closes_only_after_settle_seconds_of_quiet() -> None:
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg())

    clock.now += SETTLE - 1
    assert await trigger.close_due_windows() == 0
    assert speak.calls == []

    clock.now += 2
    assert await trigger.close_due_windows() == 1
    assert len(speak.calls) == 1


async def test_a_message_before_settle_extends_the_window() -> None:
    """Quiescence is measured from the LAST message: a burst is one window,
    not one window per message."""
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg(body="one"))
    clock.now += SETTLE - 1
    trigger.note_message(_msg(body="two"))

    clock.now += SETTLE - 1
    assert await trigger.close_due_windows() == 0

    clock.now += 2
    assert await trigger.close_due_windows() == 1
    assert len(speak.calls) == 1


async def test_one_evaluation_per_closed_window_ever() -> None:
    """The watermark is set BEFORE the evaluation, so a window that ran (or
    faulted) is closed for good — a second tick must not re-open it."""
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg())

    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1

    clock.now += 60
    assert await trigger.close_due_windows() == 0
    assert len(speak.calls) == 1


async def test_a_faulting_evaluation_still_closes_the_window() -> None:
    """A failed turn is a missed thought, not a retry loop: re-opening the
    window would make one quiet burst cost a turn every tick."""

    class _Raising:
        async def __call__(
            self, occasion, *, send_permitted: bool = True,
            gate_verdict: str | None = None, gate_score: float | None = None,
        ) -> str:
            raise RuntimeError("model down")

    clock = _Clock()
    trigger = _trigger(_Speak(), clock)
    trigger._speak = _Raising()  # the fault is the scenario
    trigger.note_message(_msg())

    clock.now += SETTLE + 1
    with contextlib.suppress(RuntimeError):
        await trigger.close_due_windows()

    clock.now += 60
    assert await trigger.close_due_windows() == 0


async def test_a_held_slot_skips_the_window_silently() -> None:
    """try_acquire-and-skip: a tag holding the conversation's slot means
    the window is marked closed and NEVER evaluated — a tag never waits behind
    an ambient thought, and the stale burst is not answered after it."""
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    key = ("signal", "g1")
    trigger.note_message(_msg())

    clock.now += SETTLE + 1
    # What a tag in flight looks like to the trigger.
    trigger._slots.acquire(key)
    assert await trigger.close_due_windows() == 1   # closed, not evaluated
    assert speak.calls == []
    trigger._slots.release_and_dispatch(key)

    clock.now += 60
    assert await trigger.close_due_windows() == 0
    assert speak.calls == []


async def test_the_cooldown_holds_the_send_not_the_evaluation() -> None:
    """Every closed window is EVALUATED, cooldown or not; only
    `send_permitted` flips. A "speak" conclusion inside the cooldown reaches
    `FirePath.speak` and is held at the `Deliverer`, logged for shadow review."""
    clock, speak = _Clock(), _Speak(outcome=SENT)
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg())
    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1
    assert speak.calls[-1][1] is True                       # first send permitted

    clock.now += 1                                          # past the window end
    trigger.note_message(_msg(body="more"))
    clock.now += SETTLE                                     # well inside COOLDOWN
    assert await trigger.close_due_windows() == 1           # STILL evaluated
    assert speak.calls[-1][1] is False                      # but the send is held

    clock.now += 1                                          # past the window end
    trigger.note_message(_msg(body="later"))
    clock.now += COOLDOWN + SETTLE + 1                      # cooldown expired
    assert await trigger.close_due_windows() == 1
    assert speak.calls[-1][1] is True


async def test_a_silent_turn_does_not_spend_the_cooldown() -> None:
    """Only a DELIVERED message spends the room's cadence: a held, silent or
    failed turn left the room exactly as it was (the cooldown caps what it HEARS)."""
    clock, speak = _Clock(), _Speak(outcome=SILENT)
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg())
    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1

    clock.now += 1                                          # past the window end
    trigger.note_message(_msg(body="more"))
    clock.now += SETTLE
    assert await trigger.close_due_windows() == 1
    assert speak.calls[-1][1] is True


async def test_the_occasion_is_personless_bodiless_and_room_shaped() -> None:
    """The occasion carries the ROOM, not a message: `person_id=None` and an
    empty body are what make the fire turn's header explain the floor offer
    rather than quote someone, and `conversation_type` comes from the
    candidate message, not re-derived from policy the trigger does not hold."""
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg(conv="g9", ctype="group"))
    clock.now += SETTLE + 1
    await trigger.close_due_windows()

    occasion, send_permitted, _gv, _gs = speak.calls[-1]
    assert occasion.kind == "participation"
    assert occasion.platform == "signal"
    assert occasion.conversation_id == "g9"
    assert occasion.conversation_type == "group"
    assert occasion.person_id is None
    assert occasion.body == ""
    assert send_permitted is True


async def test_rooms_are_independent_windows() -> None:
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg(conv="g1"))
    clock.now += SETTLE + 1
    trigger.note_message(_msg(conv="g2"))                   # g2's window is fresh

    assert await trigger.close_due_windows() == 1           # only g1 closed
    assert speak.calls[-1][0].conversation_id == "g1"


async def test_sweep_drops_stale_rooms_and_keeps_live_ones() -> None:
    """The per-room cursors are bounded exactly the way `Debounce.sweep`
    bounds its own: a room idle past the ttl forgets everything."""
    clock, speak = _Clock(), _Speak()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg(conv="g1"))
    clock.now += 50
    trigger.note_message(_msg(conv="g2"))
    clock.now += 50

    assert trigger.sweep(older_than=80) == 1                # g1 idle 100, g2 idle 50
    assert len(trigger) == 1

    clock.now += 100
    assert trigger.sweep(older_than=80) == 1
    assert len(trigger) == 0


# --- the Relevance Gate in front of the turn -------------------


class _Gate:
    """Scripted `RelevanceGate`. Records what it was asked about."""

    def __init__(self, admits: bool, score: float = 0.8) -> None:
        self._admits, self._score = admits, score
        self.asked: list[tuple[str, str]] = []

    async def decide(self, *, platform: str, conversation_id: str):
        self.asked.append((platform, conversation_id))
        return _Verdict(self._admits, self._score)


class _Verdict:
    def __init__(self, admits: bool, score: float) -> None:
        self.admits, self.score = admits, score
        self.verdict = "go" if admits else "no_go"
        # A CHOSEN verdict carries no error kind — the fail-closed half is
        # the gate's own. Kept on the fake for shape-parity with the real
        # `GateVerdict`, though the trigger itself reads neither field (DEC-179: it never writes a
        # decision-log row from the gate's verdict).
        self.error_kind = None


class _ShadowPost:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    async def __call__(self, *, platform: str, conversation_id: str, score: float):
        self.posts.append(
            {"platform": platform, "conversation_id": conversation_id, "score": score},
        )


def _gated(speak, clock, gate, shadow=None) -> ParticipationTrigger:
    return ParticipationTrigger(
        speak=speak,
        slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=COOLDOWN, clock=clock,
        gate=gate, gate_shadow=shadow,
    )


async def test_a_no_go_verdict_runs_no_turn_at_all():
    """The whole point of the gate: the expensive turn is not paid for on a
    window the cheap call already declined."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=False)
    trigger = _gated(speak, clock, gate)
    trigger.note_message(_msg())
    clock.now += SETTLE

    assert await trigger.close_due_windows() == 1
    assert gate.asked == [("signal", "g1")], "the window WAS judged"
    assert speak.calls == [], "and no turn ran"


async def test_a_go_verdict_runs_the_turn_and_carries_the_score():
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=True, score=0.73)
    trigger = _gated(speak, clock, gate)
    trigger.note_message(_msg())
    clock.now += SETTLE

    await trigger.close_due_windows()
    assert len(speak.calls) == 1
    occasion, _permitted, _gv, _gs = speak.calls[0]
    header = occasion.header(None, None)
    assert "0.73" in header, "the gate's score reached the turn"
    assert "stay silent anyway if you hold nothing" in header, (
        "and it reached her as a reference, not as an instruction to speak"
    )


async def test_the_score_is_absent_from_the_header_when_no_gate_is_wired():
    """`gate=None` admits unconditionally — the pre-gate behaviour the eval
    harness needs — and must NOT render a fabricated 0.00, which would put
    «this room scored zero» in front of her."""
    speak, clock = _Speak(), _Clock()
    trigger = _trigger(speak, clock)
    trigger.note_message(_msg())
    clock.now += SETTLE

    await trigger.close_due_windows()
    occasion, _, _gv, _gs = speak.calls[0]
    assert "relevance check upstream" not in occasion.header(None, None)


async def test_shadow_mode_posts_the_go_and_never_runs_a_turn():
    """Shadow means Nora is NEVER involved."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=True, score=0.91)
    shadow = _ShadowPost()
    trigger = _gated(speak, clock, gate, shadow=shadow)
    trigger.note_message(_msg())
    clock.now += SETTLE

    await trigger.close_due_windows()
    assert speak.calls == [], "shadow ran a turn — the room could have heard it"
    assert shadow.posts == [
        {"platform": "signal", "conversation_id": "g1", "score": 0.91},
    ]


async def test_shadow_mode_never_posts_a_no_go():
    """Only GO reaches the review channel. The no_go rate stays
    observable on the gate's own counter, not on the channel."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=False)
    shadow = _ShadowPost()
    trigger = _gated(speak, clock, gate, shadow=shadow)
    trigger.note_message(_msg())
    clock.now += SETTLE

    await trigger.close_due_windows()
    assert shadow.posts == [] and speak.calls == []


async def test_shadow_mode_never_spends_the_send_cooldown():
    """A reviewed GO left the room untouched, so the next window must still
    be reviewable. Spending the cooldown on a post nobody heard would make
    the shadow sample sparser than the live path it is predicting."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=True)
    shadow = _ShadowPost()
    trigger = _gated(speak, clock, gate, shadow=shadow)
    for _ in range(3):
        # A tick BEFORE the message, so each new window opens strictly after
        # the previous one closed — `close_due_windows` requires
        # `last_message_at > last_window_end`, and noting at exactly the
        # previous close would reuse a window that is already spent.
        clock.now += 1
        trigger.note_message(_msg())
        clock.now += SETTLE
        await trigger.close_due_windows()
    assert len(shadow.posts) == 3


async def test_a_window_the_slot_skips_is_never_even_judged():
    """try_acquire-and-skip happens BEFORE the gate call: a tag is
    already answering, so there is nothing to spend a model call on — and
    shadow keeps the same order so the reviewed GO rate matches live."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=True)
    slots = ConversationSlots(maxlen=1, spawn=lambda coro: None)
    trigger = ParticipationTrigger(
        speak=speak, slots=slots, settle_seconds=SETTLE,
        cooldown_seconds=COOLDOWN, clock=clock, gate=gate,
    )
    trigger.note_message(_msg())
    clock.now += SETTLE
    assert slots.try_acquire(("signal", "g1")), "the tag takes the slot first"
    try:
        await trigger.close_due_windows()
    finally:
        with contextlib.suppress(Exception):
            slots.release_and_dispatch(("signal", "g1"))
    assert gate.asked == [] and speak.calls == []


async def test_the_slot_is_held_across_the_gate_call_not_just_before_it():
    """The duration, not the ordering — and the ordering test above passes
    either way.

    This is the property that makes the gate's own timeout argument mean
    anything: `_RelevanceGateModelClient`'s docstring justifies its
    `asyncio.wait_for` with "this call runs while the conversation's slot is
    HELD, so an unbounded one is not a slow background job, it is a room that
    stops answering". If the slot were released around the model call, a tag
    could take it mid-gate and the gate would then run a turn on a
    conversation that has already moved on — exactly the re-answer R-30
    exists to prevent.
    """
    slots = ConversationSlots(maxlen=1, spawn=lambda coro: None)
    seen: list[bool] = []

    class _WatchingGate(_Gate):
        async def decide(self, *, platform: str, conversation_id: str):
            seen.append(slots.held((platform, conversation_id)))
            return await super().decide(
                platform=platform, conversation_id=conversation_id,
            )

    speak, clock = _Speak(), _Clock()
    trigger = ParticipationTrigger(
        speak=speak, slots=slots, settle_seconds=SETTLE,
        cooldown_seconds=COOLDOWN, clock=clock, gate=_WatchingGate(admits=True),
    )
    trigger.note_message(_msg())
    clock.now += SETTLE
    await trigger.close_due_windows()

    assert seen == [True], "the slot was not held while the gate was deciding"
    assert not slots.held(("signal", "g1")), "and it must be released after"


async def test_a_declined_window_is_closed_and_never_re_judged():
    """A no_go must burn the watermark. Re-opening the window would make one
    quiet burst cost a gate call every tick until somebody spoke again — and
    the send cooldown, which only the SEND path spends, would not cap it."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=False)
    trigger = _gated(speak, clock, gate)
    trigger.note_message(_msg())
    clock.now += SETTLE

    assert await trigger.close_due_windows() == 1
    assert await trigger.close_due_windows() == 0
    assert len(gate.asked) == 1


async def test_a_declined_window_does_not_spend_the_send_cooldown():
    """Nothing reached the room, so the next GO must still be allowed to
    speak. Spending the cooldown on a window nobody heard would silently
    halve the live path's voice."""
    speak, clock = _Speak(), _Clock()
    gate = _Gate(admits=False)
    trigger = _gated(speak, clock, gate)
    trigger.note_message(_msg())
    clock.now += SETTLE
    await trigger.close_due_windows()

    gate._admits = True
    clock.now += 1
    trigger.note_message(_msg())
    clock.now += SETTLE
    await trigger.close_due_windows()

    assert len(speak.calls) == 1
    _occasion, permitted, _gv, _gs = speak.calls[0]
    assert permitted is True, "the declined window burned the cooldown"


async def test_a_hot_room_is_judged_on_volume_not_only_on_quiet():
    """The quiet arm alone leaves a room that never pauses unjudged.

    `close_due_windows` reads `now - last_message_at >= settle_seconds`, and that resets on EVERY
    message — so a conversation whose gaps stay under `settle_seconds` never closes a window at all:
    200 messages six seconds apart would produce ZERO evaluations until the room fell silent, by
    which point the question has scrolled away. The gate would be blind exactly when a room is
    busiest.

    The two arms are OR, whichever comes first, the shape a message queue
    uses. The window watermark is a separate guard and not a third condition:
    without it a quiet room would be re-judged on every tick forever.
    """
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=False)
    trigger = ParticipationTrigger(
        speak=speak, slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=COOLDOWN, clock=clock, gate=gate,
        max_window_messages=10,
    )
    for _ in range(30):
        trigger.note_message(_msg())
        clock.now += 6                       # every gap is under SETTLE
        await trigger.close_due_windows()

    assert len(gate.asked) == 3, (
        "a room that never pauses was never judged — the volume arm is the "
        "only thing that closes a window here"
    )


async def test_the_volume_counter_resets_when_a_window_closes():
    """A window that has been judged is spent, however it closed — the same
    reason `_last_window_end` exists. Without the reset the counter stays over
    the cap and every later tick re-fires."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=False)
    trigger = ParticipationTrigger(
        speak=speak, slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=COOLDOWN, clock=clock, gate=gate,
        max_window_messages=5,
    )
    for _ in range(5):
        trigger.note_message(_msg())
        clock.now += 1
    assert await trigger.close_due_windows() == 1
    # No new messages: the counter is spent, so nothing re-fires.
    assert await trigger.close_due_windows() == 0
    assert len(gate.asked) == 1


async def test_a_quiet_room_still_closes_on_the_timeout_arm():
    """The positive control: OR, not a replacement. Three messages far apart
    never reach the volume cap and must still be judged."""
    speak, clock, gate = _Speak(), _Clock(), _Gate(admits=False)
    trigger = ParticipationTrigger(
        speak=speak, slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=COOLDOWN, clock=clock, gate=gate,
        max_window_messages=10,
    )
    trigger.note_message(_msg())
    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1
    assert len(gate.asked) == 1


# --- the shared decision log (DEC-179) ------------------------


def test_the_trigger_records_nothing_to_a_decision_log():
    """**DEC-179: never the gate.** `RelevanceGate` is «ONE call, transcript + standing, no tools,
    no doctrine, no loop, JSON out» — a writer with a fail-soft path and a store read is not that
    shape, and the gate may not read a block of what she has already raised (a pending proactive row
    carries a `note_title`). DEC-179 holds that the gate's admission control stays out of the
    decision-log seam on BOTH sides, so the trigger has no decision-log collaborator.

    Asserted structurally rather than behaviourally: the test-seam policy
    (AGENTS.md) is that a production class takes every collaborator through
    `__init__` and nothing else, so the only way this trigger could write a
    decision row again is for a constructor parameter naming one to exist.

    Falsification: give `ParticipationTrigger.__init__` a `decisions`
    parameter (or any other decision-log collaborator) and this goes red.
    """
    params = inspect.signature(ParticipationTrigger.__init__).parameters
    assert not any("decision" in name for name in params)


# --- surviving a restart ----------------------------------


class _FakeStore:
    """In-memory `ParticipationStateStore`, with `SqlParticipationStateStore`'s
    semantics: an upsert per verb, an ABSENT send rather than a zero one, and a
    `messages` half the test sets directly — that column is re-derived from the
    record at hydration precisely because the dispatcher may not await a write
    for it.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], list] = {}      # [window_end, send]
        self.messages: dict[tuple[str, str], tuple[int, str]] = {}
        self.reads = 0
        self.fail_reads = 0

    async def read_cursors(self) -> list[ParticipationCursor]:
        self.reads += 1
        if self.fail_reads:
            self.fail_reads -= 1
            raise RuntimeError("database down")
        out = []
        for (platform, conversation_id), (end, send) in self.rows.items():
            newest = self.messages.get((platform, conversation_id))
            out.append(ParticipationCursor(
                platform=platform, conversation_id=conversation_id,
                last_window_end_at=end, last_send_at=send,
                last_message_at=None if newest is None else newest[0],
                conversation_type=None if newest is None else newest[1],
            ))
        return out

    async def record_window_end(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None:
        row = self.rows.setdefault((platform, conversation_id), [at_ms, None])
        row[0] = at_ms

    async def record_send(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None:
        row = self.rows.setdefault((platform, conversation_id), [at_ms, None])
        row[1] = at_ms

    async def unfolded_candidates(self) -> list[dict]:
        # No bridge_work table backs this fake: a unit store never noted a
        # durable candidate, so hydration always finds the set empty.
        return []

    async def noted_rooms(self) -> list[tuple[str, str]]:
        # Same reason as above: no ledger membership ever existed here.
        return []


def _stored(speak: _Speak, clock: _Clock, store) -> ParticipationTrigger:
    """A trigger over a store — the production shape. `_trigger` (no store) is
    the dormant default every other test in this file uses."""
    return ParticipationTrigger(
        speak=speak,
        slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=COOLDOWN, clock=clock,
        store=store,
    )


async def test_the_send_cooldown_survives_a_restart() -> None:
    """The cooldown survives a restart. `nora` is force-recreated routinely; if the cooldown died
    with the process she could speak again at once however recently she had spoken, and the
    cooldown's whole job is to cap what a room HEARS."""
    store, clock = _FakeStore(), _Clock()
    first = _stored(_Speak(outcome=SENT), clock, store)
    first.note_message(_msg())
    clock.now += SETTLE + 1
    assert await first.close_due_windows() == 1

    # THE RESTART: a brand-new trigger with empty dicts, same store.
    speak = _Speak(outcome=SENT)
    second = _stored(speak, clock, store)
    clock.now += 1
    second.note_message(_msg(body="right after the recreate"))
    clock.now += SETTLE
    assert await second.close_due_windows() == 1, "the window is still evaluated"
    assert speak.calls[-1][1] is False, "but the send is held by the restored cooldown"


async def test_the_cooldown_expires_normally_across_a_restart() -> None:
    """Restored, not frozen: the cooldown still runs out on the wall clock."""
    store, clock = _FakeStore(), _Clock()
    first = _stored(_Speak(outcome=SENT), clock, store)
    first.note_message(_msg())
    clock.now += SETTLE + 1
    assert await first.close_due_windows() == 1

    speak = _Speak(outcome=SENT)
    second = _stored(speak, clock, store)
    clock.now += COOLDOWN
    second.note_message(_msg(body="much later"))
    clock.now += SETTLE
    assert await second.close_due_windows() == 1
    assert speak.calls[-1][1] is True


async def test_a_judged_window_is_not_re_judged_after_a_restart() -> None:
    """The watermark and the record together. `_last_message_at` is re-derived
    from `messages` at hydration, so a burst that was already answered reads as
    `last <= last_window_end` and is skipped — where two empty dicts cancel out
    and a second turn runs on a conversation that has already had one."""
    store, clock = _FakeStore(), _Clock()
    noted_at = clock.now
    first = _stored(_Speak(), clock, store)
    first.note_message(_msg())
    clock.now += SETTLE + 1
    assert await first.close_due_windows() == 1
    # What the record holds: that same burst, nothing since.
    store.messages[("signal", "g1")] = (int(noted_at * 1000), "group")

    speak = _Speak()
    second = _stored(speak, clock, store)
    clock.now += 600                                  # long past any settle
    assert await second.close_due_windows() == 0
    assert speak.calls == []


async def test_a_burst_that_arrived_after_the_last_window_still_closes() -> None:
    """The other half of the same read: traffic the old process never judged is
    not lost to the restart, it is picked up from the record."""
    store, clock = _FakeStore(), _Clock()
    first = _stored(_Speak(), clock, store)
    first.note_message(_msg())
    clock.now += SETTLE + 1
    assert await first.close_due_windows() == 1
    # A message arrived AFTER that window closed and before the recreate.
    store.messages[("signal", "g1")] = (int((clock.now + 1) * 1000), "dm")

    speak = _Speak()
    second = _stored(speak, clock, store)
    clock.now += SETTLE + 2
    assert await second.close_due_windows() == 1
    assert speak.calls[0][0].conversation_type == "dm", "and the room's type with it"


async def test_hydration_never_rewinds_a_message_noted_since_boot() -> None:
    """The dispatcher can note a message before the first tick, and a live note
    is NEWER than anything the record can say. Restoring over it would move the
    cursor backwards and re-open a window that is already accumulating."""
    store, clock = _FakeStore(), _Clock()
    store.rows[("signal", "g1")] = [int(clock.now * 1000), None]
    store.messages[("signal", "g1")] = (int(clock.now * 1000), "group")

    speak = _Speak()
    trigger = _stored(speak, clock, store)
    clock.now += 100
    trigger.note_message(_msg(body="live"))            # newer than the record
    clock.now += SETTLE - 1
    assert await trigger.close_due_windows() == 0, "the live note holds the window open"
    clock.now += 2
    assert await trigger.close_due_windows() == 1


async def test_the_store_is_read_once_per_process() -> None:
    """Hydration is a restart concern, not a tick concern: a read on every tick
    would be a query every `tick_seconds` against a table that changes when a
    window closes."""
    store, clock = _FakeStore(), _Clock()
    trigger = _stored(_Speak(), clock, store)
    for _ in range(3):
        clock.now += 1
        await trigger.close_due_windows()
    assert store.reads == 1


async def test_a_failed_hydration_is_retried_rather_than_assumed() -> None:
    """`_hydrated` is set only after the read returns. A trigger that carried
    on with half a cursor set would re-judge exactly the windows this table
    exists to protect — so the tick fails (the runtime loop logs and counts it)
    and the next one tries again."""
    store, clock = _FakeStore(), _Clock()
    # A window judged, and a send spent, one second before this process began.
    then = int((clock.now - 1) * 1000)
    store.rows[("signal", "g1")] = [then, then]
    store.messages[("signal", "g1")] = (then, "group")
    store.fail_reads = 1
    speak = _Speak(outcome=SENT)
    trigger = _stored(speak, clock, store)

    with pytest.raises(RuntimeError):
        await trigger.close_due_windows()

    trigger.note_message(_msg(body="after the blip"))
    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1
    assert speak.calls[-1][1] is False, "the restored cooldown still held the send"


async def test_a_never_sent_room_hydrates_as_absent_not_as_1970() -> None:
    """A row with no send must not read as «she last spoke in 1970»: that
    permits the send too, so the bug would survive its own test."""
    store, clock = _FakeStore(), _Clock()
    store.rows[("signal", "g1")] = [int((clock.now - 1) * 1000), None]
    speak = _Speak(outcome=SENT)
    trigger = _stored(speak, clock, store)
    trigger.note_message(_msg())
    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1
    assert speak.calls[-1][1] is True


async def test_a_write_failure_never_changes_what_the_trigger_decides() -> None:
    """The in-memory cursor is authoritative for THIS process; the store is a
    mirror. A mirror that raised would turn a database blip into a swallowed
    turn."""

    class _Refusing(_FakeStore):
        async def record_window_end(self, **kwargs) -> None:
            raise RuntimeError("write failed")

        async def record_send(self, **kwargs) -> None:
            raise RuntimeError("write failed")

    speak, clock = _Speak(outcome=SENT), _Clock()
    trigger = _stored(speak, clock, _Refusing())
    trigger.note_message(_msg())
    clock.now += SETTLE + 1
    assert await trigger.close_due_windows() == 1
    assert len(speak.calls) == 1


async def test_without_a_store_nothing_survives_a_restart() -> None:
    """The dormant default, pinned rather than assumed: with `store=None` this class keeps its
    cursors in memory only — every offline and eval wiring gets that, and a
    change to it would be a change to what they measure."""
    clock = _Clock()
    first = _trigger(_Speak(outcome=SENT), clock)
    first.note_message(_msg())
    clock.now += SETTLE + 1
    assert await first.close_due_windows() == 1

    speak = _Speak(outcome=SENT)
    second = _trigger(speak, clock)
    clock.now += 1
    second.note_message(_msg(body="right after the recreate"))
    clock.now += SETTLE
    assert await second.close_due_windows() == 1
    assert speak.calls[-1][1] is True, "no store, no memory — today's behaviour"


async def test_the_default_clock_reads_an_absolute_instant() -> None:
    """Not `time.monotonic`. Both clocks are `Callable[[], float]` and every
    comparison in this class is a difference, so nothing else in the suite
    would notice the substitution — but the two cursors are written down as
    epoch milliseconds, and a monotonic epoch is arbitrary per process,
    so a stored monotonic value is not a time to whatever reads it back.

    Asserted against `time.time()` rather than against the function object: the
    property that matters is «this reading names an instant the next process
    will agree with», and a monotonic clock is a process uptime, which on any
    machine that has been up for more than a few seconds is nowhere near it.
    """
    trigger = ParticipationTrigger(
        speak=_Speak(),
        slots=ConversationSlots(maxlen=1, spawn=lambda coro: None),
        settle_seconds=SETTLE, cooldown_seconds=COOLDOWN,
    )
    assert abs(trigger._clock() - time.time()) < 5.0
