"""`ReminderStore` over SQLAlchemy Core. Role: the heartbeat's fire queue. No
scheduling and no policy live here — this module only stores rows and moves
them between states.

Shaped like `nora.infra.sql.audit`: the engine comes through `__init__`, every
write is one statement inside `async with self._engine.begin()`, and every read
is one statement inside `async with self._engine.connect()`.

TWO STATE TRANSITIONS ARE DELIBERATELY NARROWER THAN `mark`
-----------------------------------------------------------
`mark` moves any row that still exists, whatever state it is in — the heartbeat
owns a row it has already selected and must be able to close it out. `cancel`
and `sweep_firing` both add a state predicate to the WHERE clause instead:

- `cancel` matches `state = 'pending'` so a reminder that already fired cannot
  be retroactively "cancelled", and matches `conversation_id` because a uuid
  carries no scope of its own — the STORE enforces the scope, not the tool that
  was handed the uuid.
- `sweep_firing` matches `state = 'firing'` only. It is boot recovery for rows
  whose process died mid-fire, and it resolves them to FAILED rather than back
  to PENDING: at-most-once is a decision (§8.2), because nothing links a row to
  the message it may already have delivered, so re-queueing double-sends
  anything that crashed after egress.

Both report through `rowcount`, which is why the predicate can live in SQL at
all — the caller learns whether a row actually changed without a read-back.

`FakeReminderStore` (`nora/testing/reminders.py`) is the second implementation;
`tests/contract/test_reminder_store_contract.py` is the one suite both pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql.schema import REMINDERS


def _row_to_reminder(r: Row[Any]) -> Reminder:
    return Reminder(
        id=r.id,
        requester_person_id=r.requester_person_id,
        platform=r.platform,
        conversation_id=r.conversation_id,
        conversation_type=r.conversation_type,
        text=r.text,
        created_at=r.created_at,
        due_at=r.due_at,
        state=ReminderState(r.state),
        fired_at=r.fired_at,
        outcome=r.outcome,
    )


class SqlReminderStore:
    """Satisfies `nora.domain.reminders.ReminderStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, reminder: Reminder) -> None:
        stmt = sa.insert(REMINDERS).values(
            id=reminder.id,
            requester_person_id=reminder.requester_person_id,
            platform=reminder.platform,
            conversation_id=reminder.conversation_id,
            conversation_type=reminder.conversation_type,
            text=reminder.text,
            created_at=reminder.created_at,
            due_at=reminder.due_at,
            state=reminder.state.value,
            fired_at=reminder.fired_at,
            outcome=reminder.outcome,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, reminder_id: UUID) -> Reminder | None:
        stmt = sa.select(REMINDERS).where(REMINDERS.c.id == reminder_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_reminder(row)

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, oldest first.

        `limit` is the tick's batch size and is always positive in production;
        no short-circuit is spelled out here on purpose, because the fake does
        not have one either and a guard on one side only is exactly the drift
        the contract suite exists to catch.
        """
        stmt = (
            sa.select(REMINDERS)
            .where(
                REMINDERS.c.state == ReminderState.PENDING.value,
                REMINDERS.c.due_at <= now,
            )
            .order_by(REMINDERS.c.due_at.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_reminder(r) for r in rows]

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        stmt = (
            sa.select(REMINDERS)
            .where(
                REMINDERS.c.conversation_id == conversation_id,
                REMINDERS.c.state == ReminderState.PENDING.value,
            )
            .order_by(REMINDERS.c.due_at.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_reminder(r) for r in rows]

    async def count_pending(self, requester_person_id: str) -> int:
        stmt = (
            sa.select(sa.func.count())
            .select_from(REMINDERS)
            .where(
                REMINDERS.c.requester_person_id == requester_person_id,
                REMINDERS.c.state == ReminderState.PENDING.value,
            )
        )
        async with self._engine.connect() as conn:
            total = (await conn.execute(stmt)).scalar_one()
        return int(total)

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        """True if a row changed. False means it was already resolved.

        `fired_at`/`outcome` are omitted from the UPDATE when they are None
        rather than written as NULL, so a later `mark` that only moves the state
        cannot blank the stamp an earlier one set.
        """
        values: dict[str, Any] = {"state": state.value}
        if fired_at is not None:
            values["fired_at"] = fired_at
        if outcome is not None:
            values["outcome"] = outcome
        stmt = sa.update(REMINDERS).where(REMINDERS.c.id == reminder_id).values(**values)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        stmt = (
            sa.update(REMINDERS)
            .where(
                REMINDERS.c.id == reminder_id,
                REMINDERS.c.conversation_id == conversation_id,
                REMINDERS.c.state == ReminderState.PENDING.value,
            )
            .values(state=ReminderState.CANCELLED.value)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def sweep_firing(self) -> int:
        stmt = (
            sa.update(REMINDERS)
            .where(REMINDERS.c.state == ReminderState.FIRING.value)
            .values(state=ReminderState.FAILED.value, outcome="failed_error")
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def aclose(self) -> None:
        await self._engine.dispose()
