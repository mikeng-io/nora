"""`ParticipationTrigger` — the quiescence-window trigger for the participation
occasion (design/dreamer-agent-loop/35 §3.5).

WHAT THIS MODULE IS
-------------------
The ONLY place an untagged message becomes a candidate for the agent. The
dispatcher feeds it messages that `RelevanceGatePolicy` marked
`relevance_gate=True` (an untagged message in an opted-in room); everything
else in the room's traffic never reaches it. It answers one question: has
this room gone quiet for `settle_seconds` since its last message, past the
last window already evaluated? When so, it runs exactly ONE participation
turn for the whole closed window, through `FirePath.speak`, with the full
window in context (the fire path assembles the transcript itself).

THE LOCKED DECISIONS THIS ENFORCES (design §10)
-----------------------------------------------
* **try_acquire-and-skip (§10.2).** The turn takes the conversation's
  `ConversationSlots` slot only if free; a tag holding it means the window is
  marked closed and skipped, silently — a tag never waits behind an ambient
  thought, and evaluating a stale burst after the tag would answer a
  conversation that has already moved on.
* **Send cooldown, NOT an evaluation cooldown (§10.3).** Every closed window
  is evaluated, cooldown or not; the cooldown only decides
  `send_permitted` on the `speak` call — a "speak" conclusion inside the
  cooldown has its message held by the `Deliverer` at the last inch, logged
  for shadow review, and the turn still answers `SILENT`.
* **Per-room watermark state, never per-message (§3.5).** Three dicts keyed
  by `(platform, conversation_id)`: the last message seen, the last window
  end evaluated, the last send. This is the same treatment the tag path's
  `Debounce` gets — bounded by `sweep` from the runtime loop, and retained
  state is per-ROOM cursors, not per-message session state.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from nora.domain.models import NormalizedMessage
from nora.engagement.slots import ConversationSlots
from nora.ingest.relevance_gate import RelevanceGate

from .fire import SENT, participation_occasion

log = logging.getLogger("nora.app.participation")

Key = tuple[str, str]


class ParticipationTrigger:
    """One participation evaluation per quiescence-closed window, per room.

    `speak` is `FirePath.speak` itself (a bound method, like
    `HeartbeatLoop._fire` — the composition test asserts it), called as
    `speak(occasion, send_permitted=...)`; it is typed as the narrow callable
    this class uses, not the whole `FirePath`, for the same reason
    `nora.app.runtime`'s Protocols are narrow.

    `clock` is injected for the reason `Debounce`'s is: the settle window and
    the cooldown are durations, and a module that read the clock itself could
    not be tested without sleeping.
    """

    def __init__(
        self,
        *,
        speak: Callable[..., Awaitable[str]],
        slots: ConversationSlots,
        settle_seconds: float,
        cooldown_seconds: float,
        max_window_messages: int = 10,
        gate: RelevanceGate | None = None,
        gate_shadow: Callable[..., Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._speak = speak
        self._slots = slots
        # The Relevance Gate (design/dreamer-agent-loop/36). `None` is the
        # pre-gate shape kept alive for the tests that drive the turn
        # directly: with no gate every closed window runs a turn, which is
        # exactly design 35's behaviour and exactly what the gate exists to
        # stop paying for. Production always wires one.
        self._gate = gate
        # `FirePath.post_gate_verdict` when `[relevance_gate] shadow = true`.
        # Its presence IS shadow mode: set, a GO is REVIEWED and no turn runs;
        # unset, a GO runs the turn. One switch, so there is no state in which
        # the room hears something the reviewer did not.
        self._gate_shadow = gate_shadow
        self._settle_seconds = settle_seconds
        self._cooldown_seconds = cooldown_seconds
        # The VOLUME arm of the batch (see `close_due_windows`). Sized against
        # the live rooms rather than guessed: the busiest carries ~44 human
        # messages a DAY and the next ~32, so a cap at the gate's 40-row
        # transcript window would fill about once a day — i.e. it would not
        # exist. Ten is dense enough to fire inside a real conversation.
        self._max_window_messages = max_window_messages
        self._clock = clock
        self._last_message_at: dict[Key, float] = {}
        # Messages seen since this room's last evaluation — the volume arm's
        # counter. Reset when a window closes, for the same reason
        # `_last_window_end` exists: a window that has been judged is spent.
        self._unjudged: dict[Key, int] = {}
        self._last_window_end: dict[Key, float] = {}
        self._last_send_at: dict[Key, float] = {}
        # The occasion factory needs the room's conversation_type; remembered
        # from the candidate message rather than re-derived from policy, which
        # this class deliberately does not hold.
        self._conversation_type: dict[Key, str] = {}

    def note_message(self, message: NormalizedMessage) -> None:
        """Record one participation-eligible message. Cheap and synchronous:
        called from the dispatcher, which must not await anything here — the
        evaluation happens on `close_due_windows`, never per message."""
        key = (message.platform, message.conversation_id)
        self._last_message_at[key] = self._clock()
        self._unjudged[key] = self._unjudged.get(key, 0) + 1
        self._conversation_type[key] = message.conversation_type

    async def close_due_windows(self) -> int:
        """Run the ONE evaluation for every window that has closed since the
        last call; returns how many were evaluated. The runtime loop calls
        this on its tick; tests call it directly with an injected clock.

        A window closes on EITHER arm, whichever comes first — the shape a
        message queue uses (`WaitTimeSeconds` + `MaxNumberOfMessages`):

        * **quiet** — the room has said nothing for `settle_seconds`;
        * **volume** — `max_window_messages` have arrived since this room was
          last judged.

        The volume arm is not an optimisation, it closes a hole. The quiet arm
        alone reads `now - last_message_at`, and that resets on EVERY message,
        so a room whose gaps stay under `settle_seconds` never closes a window
        at all. Measured on the real trigger: 200 messages six seconds apart —
        twenty minutes of live conversation — produced ZERO evaluations, and
        the first one only came after the room fell silent, by which time the
        question had scrolled away. The gate was blind exactly when a room is
        busiest, which is when a question is most likely to be dropped.

        The watermark (`_last_window_end`) is set BEFORE the evaluation, not
        after it: exactly one evaluation per closed window, even if the turn
        faults — a failed evaluation is a missed thought, and re-opening the
        window would make one quiet burst cost a turn every tick until it
        succeeds. `_unjudged` is cleared with it, for the same reason: a
        window that has been judged is spent, however it closed.
        """
        now = self._clock()
        due = [
            key for key, last in self._last_message_at.items()
            if last > self._last_window_end.get(key, 0.0)
            and (
                now - last >= self._settle_seconds                    # quiet
                or self._unjudged.get(key, 0) >= self._max_window_messages
            )
        ]
        for key in due:
            self._last_window_end[key] = now
            self._unjudged[key] = 0
            platform, conversation_id = key
            # §10.2: take the slot only if free. A tag holding it skips the
            # window ENTIRELY (the watermark above already closed it) — never
            # queued, never waited on.
            if not self._slots.try_acquire(key):
                log.debug(
                    "participation window skipped: slot held (%s/%s)",
                    platform, conversation_id,
                )
                continue
            try:
                admitted, score = await self._admitted(platform, conversation_id)
                if not admitted:
                    # NO-GO, or the gate failed closed. Nothing runs, nothing
                    # is posted, nothing is counted here — the gate counted
                    # its own verdict, and a second count at the trigger would
                    # be the same fact in two series.
                    continue
                if self._gate_shadow is not None:
                    # SHADOW: the verdict is REVIEWED and Nora is never
                    # involved — no turn, no agent, no cooldown spent,
                    # whatever the score. The slot was still taken above, on
                    # purpose: shadow has to model live, and a shadow that
                    # skipped the slot check would show the operator a GO rate
                    # higher than the live path could ever produce.
                    await self._gate_shadow(
                        platform=platform, conversation_id=conversation_id,
                        score=score,
                    )
                    continue
                send_permitted = (
                    now - self._last_send_at.get(key, 0.0) >= self._cooldown_seconds
                )
                outcome = await self._speak(
                    participation_occasion(
                        platform=platform, conversation_id=conversation_id,
                        conversation_type=self._conversation_type.get(key, "group"),
                        relevance_score=score,
                    ),
                    send_permitted=send_permitted,
                )
                # Only a DELIVERED message spends the cooldown: a held,
                # silent or failed turn left the room exactly as it was, and
                # §10.3's cadence caps what the room HEARS.
                if outcome == SENT:
                    self._last_send_at[key] = now
            finally:
                self._slots.release_and_dispatch(key)
        return len(due)

    async def _admitted(
        self, platform: str, conversation_id: str,
    ) -> tuple[bool, float | None]:
        """`(admitted, score)`.

        Two values and not a nullable score, because "admitted with no score"
        is a real state and collapsing it into `0.0` would put
        «this room scored zero» in front of her — the opposite of what
        happened. The score is reference only (design 36 decision 5), so a
        missing one must render as nothing, never as a low one.

        The `False` case covers NO-GO and every fail-closed reason together,
        which is the ONE place they are allowed to be the same: the trigger's
        job is "run a turn or not" and both answers are "not". They are kept
        apart where it matters, on the two counters the gate itself moves.

        No gate wired admits unconditionally with no score — the pre-gate
        behaviour. A trigger that refused without a gate would make an
        optional component mandatory by silently turning the path off.
        """
        if self._gate is None:
            return True, None
        verdict = await self._gate.decide(
            platform=platform, conversation_id=conversation_id,
        )
        return verdict.admits, (verdict.score if verdict.admits else None)

    def sweep(self, older_than: float) -> int:
        """Drop rooms idle longer than `older_than` and return how many.

        All five dicts are otherwise unbounded — one entry per opted-in room
        ever seen. This is `Debounce.sweep`'s hook, for the same reason; the
        runtime loop calls it, this class never calls it itself.
        """
        now = self._clock()
        stale = [
            key for key, last in self._last_message_at.items()
            if now - last > older_than
        ]
        for key in stale:
            del self._last_message_at[key]
            self._last_window_end.pop(key, None)
            self._unjudged.pop(key, None)
            self._last_send_at.pop(key, None)
            self._conversation_type.pop(key, None)
        return len(stale)

    def __len__(self) -> int:
        """Rooms with any retained cursor — for bounded-state assertions."""
        return len(self._last_message_at)
