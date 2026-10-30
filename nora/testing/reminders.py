"""`FakeReminderStore` — the in-memory second implementation of
`nora.domain.reminders.ReminderStore`.

Bound to `nora.infra.sql.reminders.SqlReminderStore` by
`tests/contract/test_reminder_store_contract.py`, one suite parametrized over
both, exactly as CLAUDE.md requires of any port with two implementations.

WHERE THIS STORE CANNOT COAST ON BEING A DICT
---------------------------------------------
Four of the port's rules are free in Postgres and have to be spelled out here:

- **UTC.** `timestamptz` normalises whatever offset it is handed; a dict hands
  back the object it was given. Every write goes through `_put`, which calls
  `normalise_reminder`, so every read is normalised by construction.
- **The `id` tiebreak.** Python's `sorted` is stable, so ties would come back
  in dict INSERTION order — an order Postgres has no notion of.
- **Insert-only `create`.** `dict[key] = value` overwrites; a primary key does
  not.
- **The successor a `claim` mints** (DEC-85). Postgres gets all-or-nothing free
  from the transaction wrapping the UPDATE and the INSERT; here it comes from
  there being no `await` between the two, and from `create`'s duplicate check
  being the same check the primary key performs. The ARITHMETIC is not this
  store's to reimplement — `plan_successor` is the domain's, and both stores
  call it, because two stores computing a calendar independently is two
  calendars.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from nora.domain.reminders import (
    DuplicateProactiveNoteError,
    DuplicateProactiveSubjectError,
    DuplicateReminderError,
    Reminder,
    ReminderState,
    check_limit,
    ensure_utc,
    normalise_reminder,
    open_proactive_note,
    open_proactive_subject,
    plan_successor,
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
        # `uq_reminders_proactive_open_note`, spelled out: one open proactive
        # row per note. No `await` between this and `_put`, so
        # two concurrent creates cannot both pass it — the same argument
        # `claim`'s successor makes.
        note_id = open_proactive_note(reminder)
        if note_id is not None and any(
            open_proactive_note(r) == note_id for r in self._rows.values()
        ):
            raise DuplicateProactiveNoteError(note_id)
        # `uq_reminders_proactive_open_subject`, spelled out the same way:
        # one open row per (subject, action, destination).
        subject = open_proactive_subject(reminder)
        if subject is not None and any(
            open_proactive_subject(r) == subject
            and r.platform == reminder.platform
            and r.conversation_id == reminder.conversation_id
            for r in self._rows.values()
        ):
            raise DuplicateProactiveSubjectError(
                subject, reminder.platform, reminder.conversation_id,
            )
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

    async def all_pending(self, *, limit: int) -> Sequence[Reminder]:
        size = check_limit(limit)
        return sorted(
            (r for r in self._rows.values() if r.state is ReminderState.PENDING),
            key=_order,
        )[:size]

    async def count_pending(self, requester_person_id: str) -> int:
        return sum(1 for r in self._rows.values()
                   if r.requester_person_id == requester_person_id
                   and r.state is ReminderState.PENDING)

    async def pending_of(self, requester_person_id: str) -> Sequence[Reminder]:
        return sorted(
            (r for r in self._rows.values()
             if r.requester_person_id == requester_person_id
             and r.state is ReminderState.PENDING),
            key=_order,
        )

    async def history(
        self, *, requester_person_id: str, since: datetime, limit: int,
    ) -> Sequence[Reminder]:
        cutoff = ensure_utc(since, field="since")
        size = check_limit(limit)
        rows = [
            r for r in self._rows.values()
            if r.requester_person_id == requester_person_id and r.created_at >= cutoff
        ]
        # `ORDER BY created_at, id`, the same tiebreak `due()` has and for
        # the same reason: two rows minted in one millisecond must list in
        # the same order from both stores.
        return sorted(rows, key=lambda r: (r.created_at, r.id))[:size]

    async def claim(self, reminder_id: UUID, *, now: datetime) -> bool:
        """`PENDING -> FIRING` or nothing, plus a recurring row's successor.

        The dict's version of SQL's `WHERE id = :id AND state = 'pending'`:
        there is no await between the read and the write here, so the check, the
        move and the successor's insert are as atomic as one transaction is in
        Postgres — which is what the SQL side needs a transaction to achieve and
        this side gets from the event loop.

        The duplicate check is explicit rather than `self._rows[...] = ...`, so
        a successor whose id already exists raises `DuplicateReminderError` here
        exactly as the primary key raises it there. Without it the fake would
        silently overwrite the very row the derived id exists to protect.
        """
        row = self._rows.get(reminder_id)
        if row is None or row.state is not ReminderState.PENDING:
            return False
        successor = plan_successor(row, now=now)
        if successor is not None:
            if successor.id in self._rows:
                raise DuplicateReminderError(successor.id)
            self._put(successor)
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

    async def sweep_firing(self, *, now: datetime) -> int:
        stamp = ensure_utc(now, field="now")
        stuck = [r for r in self._rows.values() if r.state is ReminderState.FIRING]
        for r in stuck:
            self._put(replace(
                r, state=ReminderState.FAILED, fired_at=stamp,
                outcome="failed_error",
            ))
        return len(stuck)

    async def aclose(self) -> None:
        return None
