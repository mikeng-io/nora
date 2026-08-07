"""`FakeReminderStore` — the in-memory second implementation of
`nora.domain.reminders.ReminderStore`.

Bound to `nora.infra.sql.reminders.SqlReminderStore` by
`tests/contract/test_reminder_store_contract.py`, one suite parametrized over
both, exactly as CLAUDE.md requires of any port with two implementations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from nora.domain.reminders import Reminder, ReminderState


class FakeReminderStore:
    """In-memory ReminderStore. Passes tests/contract/test_reminder_store_contract.py."""

    def __init__(self) -> None:
        self._rows: dict[UUID, Reminder] = {}

    async def create(self, reminder: Reminder) -> None:
        self._rows[reminder.id] = reminder

    async def get(self, reminder_id: UUID) -> Reminder | None:
        return self._rows.get(reminder_id)

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        rows = [r for r in self._rows.values()
                if r.state is ReminderState.PENDING and r.due_at <= now]
        return sorted(rows, key=lambda r: r.due_at)[:limit]

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        return sorted(
            (r for r in self._rows.values()
             if r.conversation_id == conversation_id
             and r.state is ReminderState.PENDING),
            key=lambda r: r.due_at,
        )

    async def count_pending(self, requester_person_id: str) -> int:
        return sum(1 for r in self._rows.values()
                   if r.requester_person_id == requester_person_id
                   and r.state is ReminderState.PENDING)

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        row = self._rows.get(reminder_id)
        if row is None:
            return False
        self._rows[reminder_id] = replace(
            row, state=state,
            fired_at=fired_at if fired_at is not None else row.fired_at,
            outcome=outcome if outcome is not None else row.outcome,
        )
        return True

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        row = self._rows.get(reminder_id)
        if row is None or row.conversation_id != conversation_id:
            return False
        if row.state is not ReminderState.PENDING:
            return False
        self._rows[reminder_id] = replace(row, state=ReminderState.CANCELLED)
        return True

    async def sweep_firing(self) -> int:
        stuck = [r for r in self._rows.values() if r.state is ReminderState.FIRING]
        for r in stuck:
            self._rows[r.id] = replace(r, state=ReminderState.FAILED,
                                       outcome="failed_error")
        return len(stuck)

    async def aclose(self) -> None:
        return None
