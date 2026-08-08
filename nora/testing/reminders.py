"""`FakeReminderStore` — the in-memory second implementation of
`nora.domain.reminders.ReminderStore`.

Bound to `nora.infra.sql.reminders.SqlReminderStore` by
`tests/contract/test_reminder_store_contract.py`, one suite parametrized over
both, exactly as CLAUDE.md requires of any port with two implementations.

WHERE THIS STORE CANNOT COAST ON BEING A DICT
---------------------------------------------
Three of the port's rules are free in Postgres and have to be spelled out here,
and each one was a real divergence before it was:

- **UTC.** `timestamptz` normalises whatever offset it is handed; a dict hands
  back the object it was given. Every write goes through `_put`, which calls
  `normalise_reminder`, so every read is normalised by construction.
- **The `id` tiebreak.** Python's `sorted` is stable, so ties used to come back
  in dict INSERTION order — an order Postgres has no notion of.
- **Insert-only `create`.** `dict[key] = value` overwrites; a primary key does
  not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from nora.domain.reminders import (
    DuplicateReminderError,
    Reminder,
    ReminderState,
    check_limit,
    ensure_utc,
    normalise_reminder,
)


def _order(r: Reminder) -> tuple[datetime, UUID]:
    """`ORDER BY due_at, id`. `UUID.__lt__` compares `.int`, the 16 bytes
    big-endian — the same order Postgres's bytewise `uuid` comparison gives."""
    return (r.due_at, r.id)


class FakeReminderStore:
    """In-memory ReminderStore. Passes tests/contract/test_reminder_store_contract.py."""

    def __init__(self) -> None:
        self._rows: dict[UUID, Reminder] = {}

    def _put(self, reminder: Reminder) -> None:
        """The ONE write path, so the UTC rule has one enforcement point rather
        than one per mutating method."""
        self._rows[reminder.id] = normalise_reminder(reminder)

    async def create(self, reminder: Reminder) -> None:
        if reminder.id in self._rows:
            raise DuplicateReminderError(reminder.id)
        self._put(reminder)

    async def get(self, reminder_id: UUID) -> Reminder | None:
        return self._rows.get(reminder_id)

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        cutoff = ensure_utc(now, field="now")
        size = check_limit(limit)
        rows = [r for r in self._rows.values()
                if r.state is ReminderState.PENDING and r.due_at <= cutoff]
        return sorted(rows, key=_order)[:size]

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        return sorted(
            (r for r in self._rows.values()
             if r.conversation_id == conversation_id
             and r.state is ReminderState.PENDING),
            key=_order,
        )

    async def count_pending(self, requester_person_id: str) -> int:
        return sum(1 for r in self._rows.values()
                   if r.requester_person_id == requester_person_id
                   and r.state is ReminderState.PENDING)

    async def claim(self, reminder_id: UUID) -> bool:
        """`PENDING -> FIRING` or nothing. The dict's version of SQL's
        `WHERE id = :id AND state = 'pending'`: there is no await between the
        read and the write here, so the check and the move are as atomic as one
        statement is in Postgres."""
        row = self._rows.get(reminder_id)
        if row is None or row.state is not ReminderState.PENDING:
            return False
        self._put(replace(row, state=ReminderState.FIRING))
        return True

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        stamp = None if fired_at is None else ensure_utc(fired_at, field="fired_at")
        row = self._rows.get(reminder_id)
        if row is None:
            return False
        self._put(replace(
            row, state=state,
            fired_at=stamp if stamp is not None else row.fired_at,
            outcome=outcome if outcome is not None else row.outcome,
        ))
        return True

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        row = self._rows.get(reminder_id)
        if row is None or row.conversation_id != conversation_id:
            return False
        if row.state is not ReminderState.PENDING:
            return False
        self._put(replace(row, state=ReminderState.CANCELLED))
        return True

    async def sweep_firing(self) -> int:
        stuck = [r for r in self._rows.values() if r.state is ReminderState.FIRING]
        for r in stuck:
            self._put(replace(r, state=ReminderState.FAILED, outcome="failed_error"))
        return len(stuck)

    async def aclose(self) -> None:
        return None
