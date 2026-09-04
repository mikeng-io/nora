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
