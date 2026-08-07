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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.reminders import (
    DuplicateReminderError,
    Reminder,
    ReminderState,
    check_limit,
    ensure_utc,
    normalise_reminder,
)
from nora.infra.sql.schema import REMINDERS

# PostgreSQL SQLSTATE 23505, unique_violation. Matched EXACTLY rather than
# treating every `IntegrityError` as a duplicate: this table also carries
# `ck_reminders_state`, and a bad state value arrives as an `IntegrityError`
# too — reporting that as "duplicate id" would name the wrong defect.
_PG_UNIQUE_VIOLATION = "23505"


def _row_to_reminder(r: Row[Any]) -> Reminder:
    """A row, normalised on the way OUT.

    `timestamptz` already comes back UTC-aware from asyncpg, so
    `normalise_reminder` changes nothing today — it is here so the port's rule
    has one enforcement point per direction per store rather than resting on a
    driver behaviour stated nowhere in this module.
    """
    return normalise_reminder(
        Reminder(
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
    )


class SqlReminderStore:
    """Satisfies `nora.domain.reminders.ReminderStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, reminder: Reminder) -> None:
        """Insert-only, with the primary key's own unique violation translated
        to `DuplicateReminderError` so the in-memory store can be held to the
        same observable behaviour (it used to overwrite silently)."""
        row = normalise_reminder(reminder)
        stmt = sa.insert(REMINDERS).values(
            id=row.id,
            requester_person_id=row.requester_person_id,
            platform=row.platform,
            conversation_id=row.conversation_id,
            conversation_type=row.conversation_type,
            text=row.text,
            created_at=row.created_at,
            due_at=row.due_at,
            state=row.state.value,
            fired_at=row.fired_at,
            outcome=row.outcome,
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise DuplicateReminderError(row.id) from exc

    async def get(self, reminder_id: UUID) -> Reminder | None:
        stmt = sa.select(REMINDERS).where(REMINDERS.c.id == reminder_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_reminder(row)

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, `due_at` then `id` ascending.

        `limit` is the tick's batch size and is always positive in production,
        but the guard is not optional: unguarded, `LIMIT -1` raised
        `InvalidRowCountInLimitClauseError` here while the fake's `[:-1]`
        returned everything-except-the-last. `check_limit` is the ONE rule both
        stores call — a guard on one side only is exactly the drift the contract
        suite exists to catch.

        `id` breaks ties on `due_at`. Without it, equal `due_at` rows come back
        in whatever heap order the last UPDATE left behind, and a tie across the
        `limit` boundary makes the batch's membership — not merely its order —
        unspecified. Postgres compares `uuid` bytewise, which is the same order
        Python's `UUID.__lt__` produces, so the fake's key matches exactly.
        """
        stmt = (
            sa.select(REMINDERS)
            .where(
                REMINDERS.c.state == ReminderState.PENDING.value,
                REMINDERS.c.due_at <= ensure_utc(now, field="now"),
            )
            .order_by(REMINDERS.c.due_at.asc(), REMINDERS.c.id.asc())
            .limit(check_limit(limit))
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
            .order_by(REMINDERS.c.due_at.asc(), REMINDERS.c.id.asc())
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
            values["fired_at"] = ensure_utc(fired_at, field="fired_at")
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
