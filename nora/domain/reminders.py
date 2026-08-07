"""The reminders port. A reminder is a DEFERRED REQUEST: the row stores the
requester's own sentence, and at fire time it is put to Nora as a request."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


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


class ReminderStore(Protocol):
    async def create(self, reminder: Reminder) -> None: ...

    async def get(self, reminder_id: UUID) -> Reminder | None:
        """Read one row by id. Exists so the contract suite can assert the
        TERMINAL STATE of a swept or cancelled row — without it no test can
        distinguish `FIRING -> FAILED` from `FIRING -> anything else`, because
        `due()` excludes both, and the at-most-once decision rests on exactly
        that distinction."""
        ...

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, oldest first."""
        ...

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]: ...

    async def count_pending(self, requester_person_id: str) -> int: ...

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        """True if a row changed. False means it was already resolved."""
        ...

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        """Scoped on purpose: a uuid carries none of its own."""
        ...

    async def sweep_firing(self) -> int:
        """Boot recovery. FIRING rows resolve to FAILED and are never re-fired
        (at-most-once). Returns how many."""
        ...

    async def aclose(self) -> None: ...
