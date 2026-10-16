"""`ReminderStore` over SQLAlchemy Core. Role: the heartbeat's fire queue. No
scheduling and no policy live here — this module only stores rows and moves
them between states.

Shaped like `nora.infra.sql.audit`: the engine comes through `__init__`, every
write is one statement inside `async with self._engine.begin()`, and every read
is one statement inside `async with self._engine.connect()`.

THREE STATE TRANSITIONS ARE DELIBERATELY NARROWER THAN `mark`
------------------------------------------------------------
`mark` moves any row that still exists, whatever state it is in — the heartbeat
owns a row it has already CLAIMED and must be able to close it out. `claim`,
`cancel` and `sweep_firing` all add a state predicate to the WHERE clause
instead:

- `claim` matches `state = 'pending'`, which is what makes taking a row a
  compare-and-set. `due()` is a query, not a claim, and the tick awaits a turn
  per group before it reaches the later rows — so a cancel landing in that
  window must win, and it only can if the predicate is in the statement. It is
  also the one write that INSERTS: a recurring row's successor is minted in the
  same transaction (DEC-85), because the claim is the only exactly-once point in
  a reminder's life and the successor has to inherit that.
- `cancel` matches `state = 'pending'` so a reminder that already fired cannot
  be retroactively "cancelled", and matches `conversation_id` because a uuid
  carries no scope of its own — the STORE enforces the scope, not the tool that
  was handed the uuid.
- `sweep_firing` matches `state = 'firing'` only. It is boot recovery for rows
  whose process died mid-fire, and it resolves them to FAILED rather than back
  to PENDING: at-most-once is a decision, because nothing links a row to
  the message it may already have delivered, so re-queueing double-sends
  anything that crashed after egress.

Both report through `rowcount`, which is why the predicate can live in SQL at
all — the caller learns whether a row actually changed without a read-back.

`FakeReminderStore` (`nora/testing/reminders.py`) is the second implementation;
`tests/contract/test_reminder_store_contract.py` is the one suite both pass.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.reminders import (
    DuplicateProactiveNoteError,
    DuplicateReminderError,
    Recurrence,
    RecurrenceRule,
    Reminder,
    ReminderState,
    Series,
    check_limit,
    ensure_utc,
    normalise_reminder,
    open_proactive_note,
    plan_successor,
)
from nora.infra.sql.schema import REMINDERS, UQ_REMINDERS_PROACTIVE_OPEN_NOTE_WHERE

# PostgreSQL SQLSTATE 23505, unique_violation. Matched EXACTLY rather than
# treating every `IntegrityError` as a duplicate: this table also carries
# `ck_reminders_state`, and a bad state value arrives as an `IntegrityError`
# too — reporting that as "duplicate id" would name the wrong defect.
_PG_UNIQUE_VIOLATION = "23505"

# The conflict target of a proactive insert: `uq_reminders_proactive_open_note`'s
# expression and predicate, so the inferred arbiter is that index and nothing
# wider. The expression is TEXT, not `schema.py`'s column expression: that one
# compiles `->>` with a BOUND `'note_id'`, and a parameter in an ON CONFLICT
# target does not match the index's constant, so Postgres refuses to infer
# (42P10) — the reason `SqlNoteStore`'s candidate upsert spells its key the
# same way.
_OPEN_NOTE_KEY = sa.text("(payload ->> 'note_id')")
_OPEN_NOTE_WHERE = UQ_REMINDERS_PROACTIVE_OPEN_NOTE_WHERE


log = logging.getLogger("nora.infra.sql.reminders")


def _row_to_series(r: Row[Any]) -> Series | None:
    """The seven recurrence columns, back as the one optional value object.

    `recurrence = 'none'` is the ONLY thing read to decide: the all-or-nothing
    CHECK (`ck_reminders_recurrence`) is what makes that safe, so this does not
    re-derive the invariant from six `IS NULL` tests the database already
    refuses to violate. `Series`/`RecurrenceRule` then validate what they were
    handed — an unknown zone or an out-of-range occurrence raises here rather
    than becoming a reminder nobody can compute a successor for.
    """
    if r.recurrence == Recurrence.NONE.value:
        return None
    return Series(
        id=r.series_id,
        anchor_at=r.series_anchor_at,
        occurrence=r.occurrence,
        rule=RecurrenceRule(
            every=Recurrence(r.recurrence),
            tz=r.recurrence_tz,
            count=r.recurrence_count,
            until=r.recurrence_until,
            interval=r.recurrence_interval,
        ),
    )


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
            series=_row_to_series(r),
            kind=r.kind,
            payload=r.payload,
        )
    )


def _insert_values(row: Reminder) -> dict[str, Any]:
    """One row's INSERT payload — shared by `create` and by `claim`'s successor.

    ONE builder, not two, and that is the point: the successor is written by a
    different method than the first occurrence, and a column added to one path
    but not the other would produce a series whose second occurrence quietly
    lost its recurrence and stopped. `series` is destructured here rather than
    in each caller for the same reason.
    """
    series = row.series
    return {
        "id": row.id,
        "requester_person_id": row.requester_person_id,
        "platform": row.platform,
        "conversation_id": row.conversation_id,
        "conversation_type": row.conversation_type,
        "text": row.text,
        "created_at": row.created_at,
        "due_at": row.due_at,
        "state": row.state.value,
        "fired_at": row.fired_at,
        "outcome": row.outcome,
        "recurrence": (
            Recurrence.NONE.value if series is None else series.rule.every.value
        ),
        "recurrence_tz": None if series is None else series.rule.tz,
        "recurrence_count": None if series is None else series.rule.count,
        # 1 rather than None on a one-shot: the column is NOT NULL (it is
        # meaningful on every row, unlike the nullable recurrence fields around
        # it) and 1 is the identity for a step that never repeats.
        "recurrence_interval": 1 if series is None else series.rule.interval,
        "recurrence_until": None if series is None else series.rule.until,
        "series_id": None if series is None else series.id,
        "series_anchor_at": None if series is None else series.anchor_at,
        "occurrence": 0 if series is None else series.occurrence,
        "kind": row.kind,
        "payload": row.payload,
    }


class SqlReminderStore:
    """Satisfies `nora.domain.reminders.ReminderStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, reminder: Reminder) -> None:
        """Insert-only, with the primary key's own unique violation translated
        to `DuplicateReminderError` so the in-memory store can be held to the
        same observable behaviour.

        A proactive row on a note is inserted `ON CONFLICT DO NOTHING` against
        `uq_reminders_proactive_open_note`: the statement decides,
        so two decisions racing for one note cannot both land, and a row that
        did not land is `DuplicateProactiveNoteError`, never a silent no-op.
        The conflict target names that index alone, so a primary-key collision
        still raises and translates as a duplicate id."""
        row = normalise_reminder(reminder)
        note_id = open_proactive_note(row)
        stmt = pg_insert(REMINDERS).values(**_insert_values(row))
        if note_id is not None:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[_OPEN_NOTE_KEY], index_where=_OPEN_NOTE_WHERE,
            )
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt)
                # Nothing landed. Postgres checks the ARBITER first, so a row
                # whose id AND note both collide is skipped here rather than
                # raising on the primary key; the id is the more basic fault
                # and the one the in-memory store names, so it is asked first.
                taken = note_id is not None and not result.rowcount and (
                    await conn.execute(
                        sa.select(REMINDERS.c.id).where(REMINDERS.c.id == row.id),
                    )
                ).first() is not None
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise DuplicateReminderError(row.id) from exc
        if taken:
            raise DuplicateReminderError(row.id)
        if note_id is not None and not result.rowcount:
            raise DuplicateProactiveNoteError(note_id)

    async def get(self, reminder_id: UUID) -> Reminder | None:
        stmt = sa.select(REMINDERS).where(REMINDERS.c.id == reminder_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_reminder(row)

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, `due_at` then `id` ascending.

        `limit` is the tick's batch size and is always positive in production,
        but the guard is not optional: unguarded, `LIMIT -1` raises
        `InvalidRowCountInLimitClauseError` here while the fake's `[:-1]`
        returns everything-except-the-last. `check_limit` is the ONE rule both
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
        return self._decode_due(rows)

    def _decode_due(self, rows: Sequence[Any]) -> list[Reminder]:
        """Rows → `Reminder`s, SKIPPING any single row that cannot be decoded.

        Per-row rather than a comprehension.
        `_row_to_reminder` raises on a `recurrence_tz` this host's tzdb cannot
        resolve — the column is free `Text` and no CHECK constraint can validate
        an IANA name — and that raise escapes `due()` into `HeartbeatLoop.tick`
        ABOVE its per-group `try`, whose docstring promises that one bad reminder
        must not strand the others. One unreadable row therefore stopped EVERY
        reminder firing, in every conversation, on every tick, until repaired by
        hand — visible only as `nora_errors_total{stage="heartbeat"}`.

        Reachability is conditional today (the tools validate the zone at write
        time, so it needs an out-of-band edit or a tzdata rename dropping a zone
        this row was written with), which is exactly the shape of thing that
        arrives once and is then permanent. Skipping is the fail-closed answer:
        the bad row never fires, every other row still does, and the operator
        gets a named log line instead of a dead heartbeat."""
        out: list[Reminder] = []
        for row in rows:
            try:
                out.append(_row_to_reminder(row))
            except Exception:
                log.exception(
                    "skipping an undecodable reminder row (id=%s); the rest of "
                    "this tick is unaffected", getattr(row, "id", "?"),
                )
        return out

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

    async def all_pending(self, *, limit: int) -> Sequence[Reminder]:
        """Every PENDING row, `due_at` then `id` ascending — the operator's view.

        `idx_reminders_due` (`state, due_at`) serves this exactly as it serves
        `due()`; the only difference is that there is no `due_at <= now`.
        """
        stmt = (
            sa.select(REMINDERS)
            .where(REMINDERS.c.state == ReminderState.PENDING.value)
            .order_by(REMINDERS.c.due_at.asc(), REMINDERS.c.id.asc())
            .limit(check_limit(limit))
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

    async def pending_of(self, requester_person_id: str) -> Sequence[Reminder]:
        stmt = (
            sa.select(REMINDERS)
            .where(
                REMINDERS.c.requester_person_id == requester_person_id,
                REMINDERS.c.state == ReminderState.PENDING.value,
            )
            .order_by(REMINDERS.c.due_at.asc(), REMINDERS.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return self._decode_due(rows)

    async def history(
        self, *, requester_person_id: str, since: datetime, limit: int,
    ) -> Sequence[Reminder]:
        """One requester's rows since `since`, every state, oldest first —
        the decision log. Same per-row
        decode discipline as `due()`: one undecodable row is skipped and
        named, never a dead read."""
        stmt = (
            sa.select(REMINDERS)
            .where(
                REMINDERS.c.requester_person_id == requester_person_id,
                REMINDERS.c.created_at >= ensure_utc(since, field="since"),
            )
            .order_by(REMINDERS.c.created_at.asc(), REMINDERS.c.id.asc())
            .limit(check_limit(limit))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return self._decode_due(rows)

    async def claim(self, reminder_id: UUID, *, now: datetime) -> bool:
        """`PENDING -> FIRING`, decided by the statement itself — and, for a
        recurring row, the successor written in the SAME TRANSACTION.

        The third narrowed transition: `mark` matches on id alone, so taking a
        row with it would overwrite a cancel that landed while an earlier
        group's turn was in flight, and the reminder would be spoken anyway.
        `rowcount` is the answer — the taker learns whether it won without a
        read-back, which is what makes this a compare-and-set rather than a
        check the next statement can invalidate.

        THE SELECT DOES NOT DECIDE ANYTHING (DEC-85). It only supplies the
        series the successor is computed from; the UPDATE's `state = 'pending'`
        predicate is still the whole decision, and the INSERT is reached only
        after `rowcount` says this caller won. Two concurrent claims under READ
        COMMITTED both read the row, then one blocks on the other's row lock and
        re-evaluates the predicate against the committed version — finds
        `firing`, matches nothing, inserts nothing. Exactly one successor,
        without a lock this module has to name.

        ONE TRANSACTION, so the pair is all-or-nothing: a crash between them
        cannot leave a claimed row with no successor (a series that ends at the
        first crash) or a successor with no claim (the same reminder twice).
        `plan_successor` derives the successor's id from `(series_id,
        occurrence)`, so even a double-mint is a primary-key violation rather
        than a duplicate reminder — the transaction rolls back, the row stays
        PENDING, and `nora_errors_total{stage="heartbeat"}` carries the fault.
        """
        take = (
            sa.update(REMINDERS)
            .where(
                REMINDERS.c.id == reminder_id,
                REMINDERS.c.state == ReminderState.PENDING.value,
            )
            .values(state=ReminderState.FIRING.value)
        )
        read = sa.select(REMINDERS).where(REMINDERS.c.id == reminder_id)
        async with self._engine.begin() as conn:
            before = (await conn.execute(read)).one_or_none()
            result = await conn.execute(take)
            if not result.rowcount:
                return False
            successor = None if before is None else plan_successor(
                _row_to_reminder(before), now=now,
            )
            if successor is not None:
                await conn.execute(
                    sa.insert(REMINDERS).values(
                        **_insert_values(normalise_reminder(successor)),
                    )
                )
        return True

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

    async def sweep_firing(self, *, now: datetime) -> int:
        """`fired_at` is stamped here too, and it is not cosmetic: it records
        when the row was HANDLED, which for a swept row is the only timestamp
        that exists — the process that set it FIRING is gone, and nothing else
        in the table says when. Every other terminal write goes through
        `_resolve` -> `mark(fired_at=now)`; this one stamps it itself."""
        stmt = (
            sa.update(REMINDERS)
            .where(REMINDERS.c.state == ReminderState.FIRING.value)
            .values(
                state=ReminderState.FAILED.value,
                fired_at=ensure_utc(now, field="now"),
                outcome="failed_error",
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def aclose(self) -> None:
        await self._engine.dispose()
