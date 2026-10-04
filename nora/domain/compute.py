"""The compute-job outbox port (Nora Compute spec §2/§4). A compute job is
NOT a deferred request repeated on a schedule (unlike `nora.domain.reminders`
— no `Series`, no recurrence): one job, one delivery, ever.

Every datetime crossing this port is UTC-aware, the same rule
`nora.domain.reminders`'s module docstring states for `ReminderStore` — kept
as a SEPARATE local rule/helper here rather than imported from that module,
since the two ports are independent even though the rule is identical.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class NaiveComputeJobDatetimeError(ValueError):
    """A datetime reached `ComputeJobStore` without a timezone."""


def ensure_utc_job(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveComputeJobDatetimeError(
            f"{field} must be timezone-aware; got naive {value!r}."
        )
    return value.astimezone(UTC)


class ComputeJobState(StrEnum):
    PENDING = "pending"
    FIRING = "firing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ComputeJob:
    """One deferred job awaiting delivery (Nora Compute spec §4). Nothing writes
    one today: the artifact tool, its only producer, was retired in S3b
    (design/sandbox-runtime decision 4), and the row is kept as the generic
    deferred-delivery outbox any worker uses (`kind` says which).

    `backend_job_id` is `ComputeBackend.launch`'s own return value — the
    handle `ComputeJobPoller` polls with. `artifact_id`, when set, means this
    job is revising an existing artifact (spec §9), matching
    `ComputeBackend.launch`'s own parameter of the same name and purpose.
    `requester_person_id`/`platform`/`conversation_id`/`conversation_type`
    are exactly what `compute_occasion` (nora/app/fire.py) needs to build an
    `Event` once the job completes — the same four fields
    `Event`/`Reminder` both already carry, for the same reason (a fire
    turn needs one Room, one policy decision, one Deliverer binding).

    `kind` names the backend that owns the row, `tag_id` the turn that launched
    it and `failure_reason` why a failed row failed (design/subagent §6).

    `task_sha256`/`context_sha256` are the SHA-256 of `task`/`context` as
    the producer actually sent them (context-channel spec §5) — computed
    once, by the producer itself, never by a backend. A caller can verify
    the unit file on disk still matches what was launched.
    """

    id: UUID
    backend_job_id: str
    task: str
    artifact_id: str | None
    platform: str
    conversation_id: str
    conversation_type: str
    requester_person_id: str
    created_at: datetime
    state: ComputeJobState
    completed_at: datetime | None
    result_url: str | None
    result_summary: str | None
    error: str | None
    task_sha256: str
    context_sha256: str
    # Which backend owns the row (design/subagent §6): the poller resolves
    # `ComputeBackend` by it. Required, no default: a producer names its kind.
    # Rows from before the column are `legacy` and terminal.
    kind: str
    # The launching turn's tag id, for the trace link.
    tag_id: str | None = None
    # Why a failed row failed (`lost`, a limit stop, a timeout); `error` stays
    # the human-readable text.
    failure_reason: str | None = None
    # The launch payload as stored, in full (JSON-shaped); `{}` for a row that had none.
    payload: Mapping[str, Any] = field(default_factory=dict)
    # What a finished result carried, kept so a job can be read back after delivery.
    result_report: str | None = None
    result_document_ids: tuple[str, ...] = ()
    # The worker's Langfuse trace id, written by the backend when known.
    trace_id: str | None = None
    # The sub-agent's checkpoint as last written (`nora.domain.checkpoint.row_payload`) and
    # when; `None` for a row from before checkpoints or a job that wrote none.
    checkpoint: Mapping[str, Any] | None = None
    checkpoint_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReadBackDocument:
    """A document a job's result named, read back by code before delivery (R-100). `url` is
    `""` when no public origin is configured: the document exists and has no link."""

    document_id: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class DocumentReadBack:
    """What code found when it read back each `document_id` a result named: the ones that
    read back, and the ids that did not (a missing one is reported missing, never linked)."""

    found: tuple[ReadBackDocument, ...] = ()
    missing: tuple[str, ...] = ()


def normalise_compute_job(job: ComputeJob) -> ComputeJob:
    from dataclasses import replace

    return replace(
        job,
        created_at=ensure_utc_job(job.created_at, field="created_at"),
        completed_at=(
            None if job.completed_at is None
            else ensure_utc_job(job.completed_at, field="completed_at")
        ),
        checkpoint_at=(
            None if job.checkpoint_at is None
            else ensure_utc_job(job.checkpoint_at, field="checkpoint_at")
        ),
    )


@runtime_checkable
class ComputeJobStore(Protocol):
    async def create(self, job: ComputeJob) -> None:
        """Insert-only: the row's fields are written exactly as given, `state`
        included. A producer
        always passes `PENDING`, but that is the CALLER's discipline — neither
        implementation enforces it, and the contract suite itself creates a
        terminal row directly. Stated as a habit rather than an invariant so
        nothing downstream reasons as though it were checked here."""
        ...

    async def get(self, job_id: UUID) -> ComputeJob | None: ...

    async def pending(self, *, limit: int) -> list[ComputeJob]:
        """Every `PENDING` row — `ComputeJobPoller`'s own batch, polled
        against `ComputeBackend` each tick. Unlike `ReminderStore.due()`,
        there is no `due_at` filter: EVERY pending row is a candidate on
        every tick, because "is it done" is a poll, not a clock comparison.
        """
        ...

    async def room_jobs(
        self, *, platform: str, conversation_id: str, kind: str, limit: int,
    ) -> list[ComputeJob]:
        """The newest `limit` rows of one `kind` launched in ONE room, newest first, any
        state. The room is the whole filter: a caller never sees another room's row
        (`subagent_list` and `subagent_read` are the readers)."""
        ...

    async def set_checkpoint(
        self, backend_job_id: str, checkpoint: Mapping[str, Any], at: datetime,
    ) -> bool:
        """Replace the checkpoint of the still-`PENDING` row a backend job belongs to (a
        worker knows its backend id, never the row's: the row is written after `launch`
        returns). False when there is no such row yet or it is no longer pending: the
        caller still holds the checkpoint in memory and writes again at its next update."""
        ...

    async def claim(self, job_id: UUID) -> bool:
        """`PENDING -> FIRING`, compare-and-set. True iff this caller took
        the row — the same at-most-once discipline `ReminderStore.claim`
        exists for, without the recurrence-successor half (a compute job
        never repeats)."""
        ...

    async def mark(
        self, job_id: UUID, state: ComputeJobState, *,
        completed_at: datetime | None = None, result_url: str | None = None,
        result_summary: str | None = None, error: str | None = None,
        failure_reason: str | None = None, result_report: str | None = None,
        result_document_ids: tuple[str, ...] | None = None,
        trace_id: str | None = None,
    ) -> bool:
        """Terminal write for a row already claimed. True if it changed."""
        ...

    async def sweep_firing(self, *, now: datetime) -> list[ComputeJob]:
        """Boot recovery: `FIRING` rows (a crash mid-delivery) resolve to
        `FAILED`, never re-delivered — at-most-once, mirroring
        `ReminderStore.sweep_firing`.

        Returns the rows it swept, not a count. They are terminal the instant
        this returns, so this is the ONLY moment anything can still find out
        who was waiting on them — see `nora.app.compute_poller.recover`."""
        ...

    async def sweep_pending(self, *, now: datetime) -> list[ComputeJob]:
        """Boot recovery for the OTHER half, and it has no `ReminderStore`
        counterpart because the two ports differ in what a `PENDING` row means.

        A pending reminder is a due TIME — durable, and a restart loses
        nothing. A pending compute job is a HANDLE into `ComputeBackend`'s
        in-process registry (`backend_job_id`), and the adapter that
        shipped kept that registry in memory only. So ANY restart — a crash, a deploy, a
        config change — leaves every surviving `PENDING` row pointing at a job
        this process has never heard of, and `poll` answers `FAILED("unknown
        job_id")` for it. Delivered, that becomes Nora telling the group in her
        own voice that their job failed, which is not something anybody knows.

        Sweeping them at boot resolves each to `FAILED` with an honest reason
        instead, and it is the same at-most-once rule `sweep_firing` holds:
        never re-delivered, never re-polled. Returns how many.

        What it does NOT do: reach the container. A sandbox that survived the
        restart runs to completion and may well register its artifact with
        Drop — the row says the delivery was lost, which is true, and says
        nothing about the container, which this process genuinely cannot see.
        """
        ...

    async def aclose(self) -> None: ...
