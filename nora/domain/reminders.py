"""The reminders port. A reminder is a DEFERRED REQUEST: the row stores the
requester's own sentence, and at fire time it is put to Nora as a request.

EVERY DATETIME CROSSING THIS PORT IS UTC-AWARE
----------------------------------------------
Both directions, every field, every argument. This is a CONTRACT, not a
convention, because the two implementations cannot agree by accident:

- A `timestamptz` column stores an instant and asyncpg hands it back with
  `tzinfo=UTC`, whatever offset went in. An in-memory dict hands back the exact
  object it was given. Two aware values for the same instant compare EQUAL, so
  `==` cannot see this — only `utcoffset()` can, and `utcoffset()` is what
  downstream formatting reads. Nora's own clock is `+08:00`
  (`nora.agent.tools._HKT`), so the same reminder printed "17:00" from one store
  and "09:00" from the other.
- A NAIVE datetime has no instant at all. asyncpg would resolve it in the CLIENT
  HOST's zone — a deployment fact, not a domain one — and the fake would keep it
  naive and then raise `TypeError` comparing it to an aware `now`. Neither is an
  answer, so `ensure_utc` REFUSES it: `NaiveDatetimeError`, loudly, at the port.

`ensure_utc`/`normalise_reminder` below are the single implementation of that
rule; both stores call them on the way in and on the way out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.errors import NoraError


class NaiveDatetimeError(ValueError):
    """A datetime reached the store without a timezone.

    A `ValueError` like `UnmatchableIdentifierError`, and for the same reason:
    the argument itself is unusable, and the only honest alternative — picking a
    zone for the caller — silently invents an instant that was never specified.
    """


class DuplicateReminderError(NoraError):
    """`create()` was handed an id that already exists.

    A `NoraError` so it carries the failure taxonomy (INFRASTRUCTURE by default
    — silent, per R-5). It exists so both stores can be held to ONE observable
    behaviour: Postgres raised `UniqueViolationError` off the primary key while
    the in-memory store silently OVERWROTE the row, which is a lost reminder
    reported as success.
    """

    def __init__(self, reminder_id: UUID) -> None:
        super().__init__(f"reminder {reminder_id} already exists")
        self.reminder_id = reminder_id


class ReminderState(StrEnum):
    PENDING = "pending"
    FIRING = "firing"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReminderCaps:
    """The three anti-runaway caps, as a DOMAIN value.

    Not `RemindersConfig` from `nora/config.py`: import-linter's "only app and
    __main__ read config" contract (`pyproject.toml`) lists `nora.agent` as a
    source module and `nora.config` as forbidden, so a config type on
    `ToolRegistry.__init__` fails `uv run lint-imports` outright. A
    `TYPE_CHECKING` guard does not rescue it — grimp's
    `exclude_type_checking_imports` defaults to False and this repo does not set
    it. `nora.app.composition` builds one from `cfg.reminders`.
    """

    max_pending_per_requester: int = 20
    min_lead_seconds: int = 120
    max_horizon_days: int = 30


@dataclass(frozen=True, slots=True)
class Reminder:
    id: UUID
    requester_person_id: str
    platform: str
    conversation_id: str
    conversation_type: str
    text: str
    created_at: datetime
    due_at: datetime
    state: ReminderState
    fired_at: datetime | None
    outcome: str | None


def ensure_utc(value: datetime, *, field: str) -> datetime:
    """The port's datetime rule, in one place. Raises on a naive input."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetimeError(
            f"{field} must be timezone-aware; got naive {value!r}. "
            "Reminders store instants, and a naive datetime names none."
        )
    return value.astimezone(UTC)


def normalise_reminder(reminder: Reminder) -> Reminder:
    """`ensure_utc` over all three of a reminder's datetimes."""
    return replace(
        reminder,
        created_at=ensure_utc(reminder.created_at, field="created_at"),
        due_at=ensure_utc(reminder.due_at, field="due_at"),
        fired_at=(
            None if reminder.fired_at is None
            else ensure_utc(reminder.fired_at, field="fired_at")
        ),
    )


def check_limit(limit: int) -> int:
    """`due(limit=...)`'s rule, in one place. A batch size below 1 is a caller
    bug, and the two stores used to answer it two different ways — Postgres
    raised `InvalidRowCountInLimitClauseError` on `LIMIT -1`, Python's `[:-1]`
    returned everything-except-the-last. Plausible-looking wrong data is the
    worse of the two, so both now refuse."""
    if limit < 1:
        raise ValueError(f"limit must be >= 1; got {limit}")
    return limit


@runtime_checkable
class ReminderStore(Protocol):
    async def create(self, reminder: Reminder) -> None:
        """Insert-only. A duplicate `id` raises `DuplicateReminderError` — it
        never overwrites, because an overwrite loses a reminder while reporting
        success. Naive datetimes raise `NaiveDatetimeError`; aware ones are
        stored normalised to UTC."""
        ...

    async def get(self, reminder_id: UUID) -> Reminder | None:
        """Read one row by id. Exists so the contract suite can assert the
        TERMINAL STATE of a swept or cancelled row — without it no test can
        distinguish `FIRING -> FAILED` from `FIRING -> anything else`, because
        `due()` excludes both, and the at-most-once decision rests on exactly
        that distinction."""
        ...

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, oldest first, `id`
        ASCENDING within an equal `due_at`.

        The tiebreak is part of the contract, not an incidental: `due_at` alone
        leaves ties to the storage engine's physical order, which an ordinary
        `mark()` UPDATE reshuffles — and with `limit`, a tie straddling the
        batch boundary makes the BATCH's membership unspecified, not just its
        order.

        `now` must be timezone-aware. `limit` is the tick's batch size and must
        be >= 1; below that raises `ValueError` rather than each store guessing
        (see `check_limit`).
        """
        ...

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        """That conversation's PENDING rows, ordered like `due()`: `due_at`
        then `id`."""
        ...

    async def count_pending(self, requester_person_id: str) -> int: ...

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        """True if a row changed. False means it was already resolved.

        `fired_at`, when given, must be timezone-aware and is stored as UTC."""
        ...

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        """Scoped on purpose: a uuid carries none of its own."""
        ...

    async def sweep_firing(self) -> int:
        """Boot recovery. FIRING rows resolve to FAILED and are never re-fired
        (at-most-once). Returns how many."""
        ...

    async def aclose(self) -> None: ...
