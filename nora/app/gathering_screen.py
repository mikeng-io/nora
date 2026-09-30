"""The fire-time screen for a `kind="gathering"` reminder row: a gathering whose
evidence was withdrawn is not announced (design/cognition-loop/10-tiers.md §2
rule 7; the review of 2026-09-30 M2 and of #662 M4, "a withdrawn row renders in
every fire turn").

A gathering's reminder is written with the event's title copied into its
payload, and the fire path composes its turn from that payload alone
(`nora.app.fire.gathering_occasion`): it never reads the event. So when the
withdrawal cascade (`nora.app.withdrawal`) marks an event `unsupported_at`, the
reminder it owns would still say the title, in the room, at the time, on the
strength of a message that is gone. This screen is the read that closes it: the
heartbeat asks it before it claims the row, the same place and the same shape as
`nora.app.proactive_screen.ProactiveScreen`.

Answers `None` (fire it) or `(state, outcome)`:
- the event is withdrawn: `CANCELLED` / `cancelled_unsupported`, the row says why;
- the event cannot be read: `FAILED` / `failed_error`, logged. «I could not
  check» is neither «it stands» (fires on an unverified premise) nor «it was
  withdrawn» (claims a fact nobody read);
- no event owns the row, or it stands: `None`. A reminder with no event behind it
  (written before the event linked it, or an event since deleted) is unaffected.
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from nora.domain.gatherings import Gathering
from nora.domain.reminders import Reminder, ReminderState

log = logging.getLogger("nora.app.gathering_screen")

GATHERING_KIND = "gathering"
CANCELLED_UNSUPPORTED = "cancelled_unsupported"
FAILED_ERROR = "failed_error"

__all__ = ["CANCELLED_UNSUPPORTED", "GATHERING_KIND", "GatheringScreen"]


class _Events(Protocol):
    """`GatheringStore.get_by_reminder`, the one read the screen needs."""

    async def get_by_reminder(self, reminder_id: UUID) -> Gathering | None: ...


class GatheringScreen:
    def __init__(self, *, events: _Events) -> None:
        self._events = events

    async def refusal(self, row: Reminder) -> tuple[ReminderState, str] | None:
        try:
            event = await self._events.get_by_reminder(row.id)
        except Exception:
            log.exception(
                "gathering reminder %s: its event could not be read; not firing "
                "on an unverified premise", row.id,
            )
            return ReminderState.FAILED, FAILED_ERROR
        if event is not None and event.unsupported_at is not None:
            log.info(
                "gathering reminder %s not fired: event %s lost its evidence", row.id, event.id,
            )
            return ReminderState.CANCELLED, CANCELLED_UNSUPPORTED
        return None
