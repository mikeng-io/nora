"""`ComputeJobStore` over SQLAlchemy Core — the Nora Compute outbox's SQL
implementation. Shaped like `nora.infra.sql.reminders`, minus everything
recurrence-related: a compute job never repeats.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    ensure_utc_job,
    normalise_compute_job,
)
from nora.infra.sql.schema import COMPUTE_JOBS


def _row_to_job(r: Row[Any]) -> ComputeJob:
    return normalise_compute_job(
        ComputeJob(
            id=r.id, backend_job_id=r.backend_job_id, task=r.task,
            artifact_id=r.artifact_id, platform=r.platform,
            conversation_id=r.conversation_id,
            conversation_type=r.conversation_type,
            requester_person_id=r.requester_person_id,
            created_at=r.created_at, state=ComputeJobState(r.state),
            completed_at=r.completed_at, result_url=r.result_url,
            result_summary=r.result_summary, error=r.error,
            task_sha256=r.task_sha256, context_sha256=r.context_sha256,
            kind=r.kind, tag_id=r.tag_id, failure_reason=r.failure_reason,
            payload=r.payload, result_report=r.result_report,
            result_document_ids=tuple(r.result_document_ids or ()), trace_id=r.trace_id,
            checkpoint=r.checkpoint, checkpoint_at=r.checkpoint_at,
        )
    )


class SqlComputeJobStore:
    """Satisfies `nora.domain.compute.ComputeJobStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, job: ComputeJob) -> None:
        row = normalise_compute_job(job)
        stmt = sa.insert(COMPUTE_JOBS).values(
            id=row.id, backend_job_id=row.backend_job_id, task=row.task,
            artifact_id=row.artifact_id, platform=row.platform,
            conversation_id=row.conversation_id,
            conversation_type=row.conversation_type,
            requester_person_id=row.requester_person_id,
            created_at=row.created_at, state=row.state.value,
            task_sha256=row.task_sha256, context_sha256=row.context_sha256,
            kind=row.kind, tag_id=row.tag_id, failure_reason=row.failure_reason,
            payload=dict(row.payload), result_report=row.result_report,
            result_document_ids=(
                list(row.result_document_ids) if row.result_document_ids else None
            ),
            trace_id=row.trace_id,
            checkpoint=None if row.checkpoint is None else dict(row.checkpoint),
            checkpoint_at=row.checkpoint_at,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, job_id: UUID) -> ComputeJob | None:
        stmt = sa.select(COMPUTE_JOBS).where(COMPUTE_JOBS.c.id == job_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_job(row)

    async def pending(self, *, limit: int) -> list[ComputeJob]:
        stmt = (
            sa.select(COMPUTE_JOBS)
            .where(COMPUTE_JOBS.c.state == ComputeJobState.PENDING.value)
            .order_by(COMPUTE_JOBS.c.created_at.asc(), COMPUTE_JOBS.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_job(r) for r in rows]

    async def room_jobs(
        self, *, platform: str, conversation_id: str, kind: str, limit: int,
    ) -> list[ComputeJob]:
        # ponytail: no (platform, conversation_id) index; the table holds a handful of rows a
        # day. Add one if `subagent_list` ever shows up in a slow-query read-back.
        stmt = (
            sa.select(COMPUTE_JOBS)
            .where(
                COMPUTE_JOBS.c.platform == platform,
                COMPUTE_JOBS.c.conversation_id == conversation_id,
                COMPUTE_JOBS.c.kind == kind,
            )
            .order_by(COMPUTE_JOBS.c.created_at.desc(), COMPUTE_JOBS.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_job(r) for r in rows]

    async def kind_since(self, *, kind: str, since: Any) -> list[ComputeJob]:
        # ponytail: no (kind, created_at) index; a day holds a handful of rows of any kind.
        stmt = (
            sa.select(COMPUTE_JOBS)
            .where(
                COMPUTE_JOBS.c.kind == kind,
                COMPUTE_JOBS.c.created_at >= ensure_utc_job(since, field="since"),
            )
            .order_by(COMPUTE_JOBS.c.created_at.asc(), COMPUTE_JOBS.c.id.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_job(r) for r in rows]

    async def set_checkpoint(self, backend_job_id: str, checkpoint: Any, at: Any) -> bool:
        # ponytail: matched by `backend_job_id` with no index; the table holds a handful of
        # rows a day and only pending ones are touched.
        stmt = (
            sa.update(COMPUTE_JOBS)
            .where(
                COMPUTE_JOBS.c.backend_job_id == backend_job_id,
                COMPUTE_JOBS.c.state == ComputeJobState.PENDING.value,
            )
            .values(
                checkpoint=dict(checkpoint),
                checkpoint_at=ensure_utc_job(at, field="checkpoint_at"),
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def claim(self, job_id: UUID) -> bool:
        stmt = (
            sa.update(COMPUTE_JOBS)
            .where(
                COMPUTE_JOBS.c.id == job_id,
                COMPUTE_JOBS.c.state == ComputeJobState.PENDING.value,
            )
            .values(state=ComputeJobState.FIRING.value)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def mark(
        self, job_id: UUID, state: ComputeJobState, *,
        completed_at: Any = None, result_url: str | None = None,
        result_summary: str | None = None, error: str | None = None,
        failure_reason: str | None = None, result_report: str | None = None,
        result_document_ids: tuple[str, ...] | None = None,
        trace_id: str | None = None,
        checkpoint: Any = None, checkpoint_at: Any = None,
    ) -> bool:
        values: dict[str, Any] = {"state": state.value}
        if completed_at is not None:
            values["completed_at"] = ensure_utc_job(completed_at, field="completed_at")
        if result_url is not None:
            values["result_url"] = result_url
        if result_summary is not None:
            values["result_summary"] = result_summary
        if error is not None:
            values["error"] = error
        if failure_reason is not None:
            values["failure_reason"] = failure_reason
        if result_report is not None:
            values["result_report"] = result_report
        if result_document_ids:
            values["result_document_ids"] = list(result_document_ids)
        if trace_id is not None:
            values["trace_id"] = trace_id
        if checkpoint is not None:
            values["checkpoint"] = dict(checkpoint)
            if checkpoint_at is not None:
                values["checkpoint_at"] = ensure_utc_job(checkpoint_at, field="checkpoint_at")
        stmt = sa.update(COMPUTE_JOBS).where(COMPUTE_JOBS.c.id == job_id).values(**values)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def sweep_firing(self, *, now: Any) -> list[ComputeJob]:
        return await self._sweep(
            ComputeJobState.FIRING, now, "swept at boot: process crashed mid-delivery", "lost",
        )

    async def sweep_pending(
        self, *, now: Any, keep_kinds: frozenset[str] = frozenset(),
    ) -> list[ComputeJob]:
        return await self._sweep(
            ComputeJobState.PENDING, now,
            "swept at boot: lost track of this job across a restart", "lost",
            keep_kinds=keep_kinds,
        )

    async def _sweep(
        self, state: ComputeJobState, now: Any, error: str, reason: str,
        *, keep_kinds: frozenset[str] = frozenset(),
    ) -> list[ComputeJob]:
        where = [COMPUTE_JOBS.c.state == state.value]
        if keep_kinds:
            where.append(COMPUTE_JOBS.c.kind.notin_(sorted(keep_kinds)))
        stmt = (
            sa.update(COMPUTE_JOBS)
            .where(*where)
            .values(
                state=ComputeJobState.FAILED.value,
                completed_at=ensure_utc_job(now, field="now"),
                error=error, failure_reason=reason,
            )
        )
        # RETURNING the rows, not just a count: the person who asked for each
        # of these was told «I'm working on it» and is still waiting, and
        # closing that loop needs their room and their name. The sweep is the
        # only place that knowledge exists — the row is terminal immediately
        # after, so nothing downstream can find them again (see
        # `nora.app.compute_poller.recover`).
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt.returning(*COMPUTE_JOBS.c))).all()
        return [_row_to_job(r) for r in rows]

    async def aclose(self) -> None:
        await self._engine.dispose()
