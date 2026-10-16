"""`ParticipationCursor` — one room's participation trigger state, as it
survives a process restart (`nora.app.participation.ParticipationTrigger`).

**Four values, and three of them can be absent.** The absent case is the whole
reason this is a type rather than a tuple of floats: epoch 0 is a real instant
in 1970 that any duration arithmetic computes against happily, so a cursor that
returned it for a room that has never sent would put "she last spoke
fifty-six years ago" in front of the cooldown, which reads as a correct answer
and permits the send either way. `None` is the honest reading and the trigger
decides what a first sighting means:

* `last_send_at is None` — she has never SENT in this room. The cooldown does
  not apply, rather than applying against 1970.
* `last_message_at is None` — the record holds no inbound message for this
  room (pruned, or the room was opted in and has not spoken since). No window
  can be due, rather than one being due since 1970.
* `conversation_type is None` — same absence, same row. The trigger keeps its
  own `"group"` default for the occasion rather than inventing one here.

`last_window_end_at` is the one that is never absent: a row exists BECAUSE a
window closed, so the value that created the row is the value it carries.

**Every timestamp is epoch MILLISECONDS on the wall clock**, matching every
other durable timestamp in this schema (`messages.ts`,
`dream_schedule_state.last_fired_at`). It is not `time.monotonic()` and cannot
be: a monotonic epoch is arbitrary per process, so persisting one produces a
number that means nothing to the process that reads it back — which is the
defect this type exists to close, not a detail of how it is stored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParticipationCursor:
    """One room's restored watermarks. See the module docstring for absence."""

    platform: str
    conversation_id: str
    #: Epoch ms of the last window this room had judged. Never absent.
    last_window_end_at: int
    #: Epoch ms of the last DELIVERED participation message, or None — never 0.
    last_send_at: int | None
    #: Epoch ms of the newest inbound (non-Nora) message the record holds for
    #: this room, or None. Derived from `messages`, never stored here: it moves
    #: on the dispatcher's synchronous path, which must not await a write.
    last_message_at: int | None
    #: `messages.conversation_type` off that same newest row, or None with it.
    conversation_type: str | None
