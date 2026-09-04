"""`ParticipationTrigger` — the quiescence-window trigger for the participation
occasion (design/dreamer-agent-loop/35 §3.5).

WHAT THIS MODULE IS
-------------------
The ONLY place an untagged message becomes a candidate for the agent. The
dispatcher feeds it messages that `ParticipationPolicy` marked
`participation=True` (an untagged message in an opted-in room); everything
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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._speak = speak
        self._slots = slots
        self._settle_seconds = settle_seconds
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._last_message_at: dict[Key, float] = {}
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
        self._conversation_type[key] = message.conversation_type

    async def close_due_windows(self) -> int:
        """Run the ONE evaluation for every window that has closed since the
        last call; returns how many were evaluated. The runtime loop calls
        this on its tick; tests call it directly with an injected clock.

        The watermark (`_last_window_end`) is set BEFORE the evaluation, not
        after it: exactly one evaluation per closed window, even if the turn
        faults — a failed evaluation is a missed thought, and re-opening the
        window would make one quiet burst cost a turn every tick until it
        succeeds.
        """
        now = self._clock()
        due = [
            key for key, last in self._last_message_at.items()
            if now - last >= self._settle_seconds
            and last > self._last_window_end.get(key, 0.0)
        ]
        for key in due:
            self._last_window_end[key] = now
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
                send_permitted = (
                    now - self._last_send_at.get(key, 0.0) >= self._cooldown_seconds
                )
                outcome = await self._speak(
                    participation_occasion(
                        platform=platform, conversation_id=conversation_id,
                        conversation_type=self._conversation_type.get(key, "group"),
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

    def sweep(self, older_than: float) -> int:
        """Drop rooms idle longer than `older_than` and return how many.

        All four dicts are otherwise unbounded — one entry per opted-in room
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
            self._last_send_at.pop(key, None)
            self._conversation_type.pop(key, None)
        return len(stale)

    def __len__(self) -> int:
        """Rooms with any retained cursor — for bounded-state assertions."""
        return len(self._last_message_at)
