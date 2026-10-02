"""The fire-time screen for a `kind="gathering"` reminder row: a gathering whose
evidence was withdrawn is not announced (design/cognition-loop/10-tiers.md §2
rule 7; the review of 2026-09-30 M2 and of #662 M4, "a withdrawn row renders in
every fire turn").

A gathering's reminder is written with the event's title copied into its
payload, and the fire path composes its turn from that payload alone
(`nora.app.fire.gathering_occasion`): it never reads the event. So when an event is
marked `unsupported_at` (nothing marks one since the cascade was removed, 2026-10-03;
only a row marked before then, or by an operator's SQL, carries it), the
reminder it owns would still say the title, in the room, at the time, on the
strength of a message that is gone. This screen is the read that closes it: the
heartbeat asks it before it claims the row, the same place and the same shape as
`nora.app.proactive_screen.ProactiveScreen`.

**Withdrawal is a hold, not a cancellation** (10 §2 rule 7: a marked row "stops
rendering", and clearing the mark reverses it). So a
reminder whose event is marked is HELD: left `PENDING`, not fired, not resolved.
The mark is the one copy of the fact (a second flag on the reminder would be a
second copy that could drift), so a cleared mark releases the reminder with
nothing else to undo.

A hold has a time limit, stated once here: **a held reminder waits until
`due_at + HOLD_GRACE`.** If the mark is cleared inside that window the reminder
fires on the next tick, late by at most the grace; if the event is still marked
once it has passed, the moment is gone and the row resolves `CANCELLED` /
`expired_while_held` (a distinct outcome: nobody withdrew the reminder, it ran
out of time while the evidence was out). A mark cleared after that does not
revive it.

Answers `None` (fire it) or `(state, outcome)`; a `state` of `None` is a hold,
and the row stays `PENDING` for the next tick:
- the event is marked, inside the grace: `(None, "unsupported")`, counted on
  `nora_gathering_reminders_held_total`;
- the event is marked, past the grace: `CANCELLED` / `expired_while_held`;
- the event cannot be read: `(None, "read_error")`, logged and counted, never
  terminal. «I could not check» is neither «it stands» (fires on an unverified
  premise) nor «it was withdrawn» (claims a fact nobody read), and a transient
  database error must not cost a legitimate reminder its life: the next tick
  reads again;
- no event owns the row, or it stands: `None`. A reminder with no event behind it
  (written before the event linked it, or an event since deleted) is unaffected.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol
from uuid import UUID

from nora.domain.gatherings import (
    HELD_READ_ERROR,
    HELD_UNSUPPORTED,
    HOLD_GRACE,
    Gathering,
)
from nora.domain.reminders import Reminder, ReminderState

log = logging.getLogger("nora.app.gathering_screen")

GATHERING_KIND = "gathering"
#: The terminal outcome a hold that outlived `HOLD_GRACE` writes
#: (`nora_reminders_fired_total{outcome}`).
EXPIRED_WHILE_HELD = "expired_while_held"

__all__ = ["EXPIRED_WHILE_HELD", "GATHERING_KIND", "GatheringScreen"]


class _Events(Protocol):
    """`GatheringStore.get_by_reminder`, the one read the screen needs."""

    async def get_by_reminder(self, reminder_id: UUID) -> Gathering | None: ...


class GatheringScreen:
    def __init__(self, *, events: _Events) -> None:
        self._events = events

    async def refusal(
        self, row: Reminder, *, now: datetime,
    ) -> tuple[ReminderState | None, str] | None:
        try:
            event = await self._events.get_by_reminder(row.id)
        except Exception:
            log.exception(
                "gathering reminder %s: its event could not be read; holding it "
                "for the next tick, not firing on an unverified premise", row.id,
            )
            return None, HELD_READ_ERROR
        if event is None or event.unsupported_at is None:
            return None
        if now >= row.due_at + HOLD_GRACE:
            log.info(
                "gathering reminder %s expired while held: event %s is still "
                "without its evidence %s past its time", row.id, event.id, HOLD_GRACE,
            )
            return ReminderState.CANCELLED, EXPIRED_WHILE_HELD
        log.info(
            "gathering reminder %s held, not fired: event %s lost its evidence",
            row.id, event.id,
        )
        return None, HELD_UNSUPPORTED
