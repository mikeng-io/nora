"""`ParticipationTrigger` — one evaluation per quiescence-closed window.

design/dreamer-agent-loop/35 §3.5/§10: a candidate window OPENS on an opted-in
room's untagged message and CLOSES when the room goes quiet for
`settle_seconds`; exactly one participation evaluation runs per closed window;
the slot is try_acquire-and-skip; the cooldown caps SENDS, never evaluations.
Every test drives `close_due_windows` directly against an injected clock —
never a sleep — the same discipline `Debounce`'s own tests keep.
"""

from __future__ import annotations

import contextlib

from nora.app.fire import SENT, SILENT
from nora.app.participation import ParticipationTrigger
from nora.domain.models import NormalizedMessage
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

    async def __call__(self, occasion, *, send_permitted: bool = True) -> str:
        self.calls.append((occasion, send_permitted))
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
        async def __call__(self, occasion, *, send_permitted: bool = True) -> str:
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
    """§10.2 try_acquire-and-skip: a tag holding the conversation's slot means
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
    """§10.3: every closed window is EVALUATED, cooldown or not; only
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
    failed turn left the room exactly as it was (§10.3 caps what it HEARS)."""
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

    occasion, send_permitted = speak.calls[-1]
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


# --- the Relevance Gate in front of the turn (design 36) -------------------


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
    """The whole point of design 36: the expensive turn is not paid for on a
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
    occasion, _permitted = speak.calls[0]
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
    occasion, _ = speak.calls[0]
    assert "relevance check upstream" not in occasion.header(None, None)


async def test_shadow_mode_posts_the_go_and_never_runs_a_turn():
    """Design 36 decision 6 + §4: shadow means Nora is NEVER involved."""
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
    """Decision 6: only GO reaches the review channel. The no_go rate stays
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
    """§10.2's try_acquire-and-skip happens BEFORE the gate call: a tag is
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
    _occasion, permitted = speak.calls[0]
    assert permitted is True, "the declined window burned the cooldown"


async def test_a_hot_room_is_judged_on_volume_not_only_on_quiet():
    """The quiet arm alone leaves a room that never pauses unjudged.

    `close_due_windows` read `now - last_message_at >= settle_seconds`, and
    that resets on EVERY message — so a conversation whose gaps stay under
    `settle_seconds` never closes a window at all. Measured on this trigger
    before the fix: 200 messages six seconds apart, twenty minutes of live
    conversation, produced ZERO evaluations; the first came only after the
    room fell silent, by which point the question had scrolled away. The gate
    was blind exactly when a room is busiest — which is when a question is
    most likely to be dropped.

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
