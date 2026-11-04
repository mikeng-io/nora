"""Computer execution tasks: the durable intent behind the scope FIFO.

`computer_tasks` (migration `0147`) is ONE execution segment on one scope's
VM: a native mutation (`computer_run`/`write`/`edit`/`put`), a browser
objective, a direct capture, or a worker's job. It is written BEFORE the
stream entry that announces it (`XADD nq:computer:<scope>`), so a publish gap
is reconcilable — an unannounced `queued` row is re-published, never lost —
and it is the segment's ONLY state: a stream entry is a pointer, never the
record (`Redis stream delivery is not execution exclusion`).

States: `queued` → `running` → `done`/`failed`/`unavailable`; `cancelled`
from queued or running. `interrupted` is the recovery state: the owner died
mid-segment, the side effect is UNCERTAIN, and the segment is closed, never
replayed (`interrupted`, not `queued` again — an uncertain command, click or
export must not run twice). A row that never reached `running` may requeue:
nothing ran, so nothing is uncertain.

`origin` is the delivery provenance: the platform, room, person and turn the
result belongs to, plus the computer session and the audit `operation_id` it
was admitted under. A worker's nested calls carry `parent`: the segment that
spawned them, so they inherit its ownership and never enqueue behind it.

Every datetime is UTC-aware.
"""

from __future__ import annotations

import contextvars
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "CANCELLED",
    "DONE",
    "FAILED",
    "INTERRUPTED",
    "QUEUED",
    "RUNNING",
    "TASK_KINDS",
    "TASK_STATUSES",
    "TERMINAL",
    "UNAVAILABLE",
    "ComputerTask",
    "ComputerTaskStore",
    "SegmentOutcome",
    "current_segment",
    "ensure_utc_event",
    "normalise_task",
]

from nora.domain.computer_events import ensure_utc_event

QUEUED, RUNNING = "queued", "running"
DONE, FAILED, CANCELLED, UNAVAILABLE, INTERRUPTED = (
    "done", "failed", "cancelled", "unavailable", "interrupted")
TASK_STATUSES: frozenset[str] = frozenset(
    {QUEUED, RUNNING, DONE, FAILED, CANCELLED, UNAVAILABLE, INTERRUPTED})
TERMINAL: frozenset[str] = frozenset({DONE, FAILED, CANCELLED, UNAVAILABLE, INTERRUPTED})
TASK_KINDS: frozenset[str] = frozenset({"native", "browse", "capture", "worker"})

#: The segment this context is executing, when inside one. A child call
#: made here inherits the parent's admission (`nora.app.computer_queue.
#: ComputerAdmission.submit`) instead of queueing behind its own parent.
current_segment: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "nora_computer_segment", default=None)

_SCOPE = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True, slots=True)
class SegmentOutcome:
    """How an executed segment answered: the status word the tool knows and
    its full result payload. Never raises: an executor failure is
    `unavailable`."""

    status: str
    result: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComputerTask:
    id: UUID
    scope: str
    kind: str
    status: str
    payload: Mapping[str, Any]
    origin: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime
    parent: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    deadline_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    delivered_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in TASK_STATUSES:
            raise ValueError(f"unknown computer task status {self.status!r}")
        if self.kind not in TASK_KINDS:
            raise ValueError(f"unknown computer task kind {self.kind!r}")
        if not _SCOPE.fullmatch(self.scope or ""):
            raise ValueError(f"computer task scope is not a hex key: {self.scope!r}")


def normalise_task(task: ComputerTask) -> ComputerTask:
    return replace(
        task,
        created_at=ensure_utc_event(task.created_at, field="created_at"),
        updated_at=ensure_utc_event(task.updated_at, field="updated_at"),
        started_at=None if task.started_at is None
        else ensure_utc_event(task.started_at, field="started_at"),
        finished_at=None if task.finished_at is None
        else ensure_utc_event(task.finished_at, field="finished_at"),
        deadline_at=None if task.deadline_at is None
        else ensure_utc_event(task.deadline_at, field="deadline_at"),
        cancel_requested_at=None if task.cancel_requested_at is None
        else ensure_utc_event(task.cancel_requested_at, field="cancel_requested_at"),
        delivered_at=None if task.delivered_at is None
        else ensure_utc_event(task.delivered_at, field="delivered_at"),
        payload=dict(task.payload), origin=dict(task.origin),
        result=dict(task.result),
    )


@runtime_checkable
class ComputerTaskStore(Protocol):
    """`computer_tasks`. Implementations:
    `nora.infra.sql.computer_tasks.SqlComputerTaskStore` and
    `nora.testing.FakeComputerTaskStore`. MAY raise on storage failure; the
    caller decides what that costs (an enqueue that cannot persist means the
    segment does NOT enter the queue)."""

    async def enqueue(self, task: ComputerTask) -> ComputerTask: ...

    async def get(self, task_id: UUID) -> ComputerTask | None: ...

    async def claim(self, task_id: UUID, *, now: datetime) -> ComputerTask | None:
        """queued → running, a compare-and-set: the row only when it is still
        `queued` at the write (permission, deadline and cancellation were
        re-read under it). None when it moved — cancelled, expired, taken."""
        ...

    async def try_claim_head(self, task_id: UUID, *, now: datetime) -> ComputerTask | None:
        """Claim ONLY if this row is the scope's head: still `queued`, and no
        other row of its scope is `running` or ahead of it in `queued`.
        The one-slot FIFO's atomic half — the submitter's fast path and the
        runner's grant share it. None when it is not head or moved."""
        ...

    async def finish(self, task_id: UUID, *, status: str,
                     result: Mapping[str, Any], now: datetime) -> None:
        """running → a terminal state, the result persisted BEFORE the stream
        entry is acknowledged. A no-op when the row already left `running`."""
        ...

    async def request_cancel(self, task_id: UUID, *, now: datetime) -> ComputerTask | None:
        """Mark a queued task cancelled, a running one cancel-requested; the
        row as it now stands. A terminal row returns itself unchanged."""
        ...

    async def unannounced(self, *, limit: int) -> list[ComputerTask]:
        """`queued` rows whose stream entry may never have been published:
        the publish-gap reconciliation read."""
        ...

    async def interrupted(self, *, older_than: datetime, limit: int) -> list[ComputerTask]:
        """`running` rows whose owner died (no finish past `older_than`):
        recovery closes them `interrupted`, never replays them."""
        ...

    async def mark_interrupted(self, task_id: UUID, *, now: datetime,
                               reason: str) -> None: ...

    async def open_for_scope(self, scope: str, *, limit: int) -> list[ComputerTask]:
        """The scope's non-terminal rows, oldest first: queue depth and the
        running segment for the context block."""
        ...

    async def open_scopes(self, *, limit: int) -> list[str]:
        """Every scope with a non-terminal row: the granter's breadth read,
        from the table — the record of truth — never a stream SCAN."""
        ...

    async def mark_delivered(self, task_id: UUID, *, now: datetime) -> None:
        """The terminal result reached its room (or the caller consumed it):
        `delivered_at`, bookkeeping only — the result row already stands."""
        ...
