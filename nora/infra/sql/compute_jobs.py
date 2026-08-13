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
        stmt = sa.update(COMPUTE_JOBS).where(COMPUTE_JOBS.c.id == job_id).values(**values)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def sweep_firing(self, *, now: Any) -> int:
        return await self._sweep(
            ComputeJobState.FIRING, now, "swept at boot: process crashed mid-delivery",
        )

    async def sweep_pending(self, *, now: Any) -> int:
        return await self._sweep(
            ComputeJobState.PENDING, now,
            "swept at boot: lost track of this job across a restart",
        )

    async def _sweep(self, state: ComputeJobState, now: Any, error: str) -> int:
        stmt = (
            sa.update(COMPUTE_JOBS)
            .where(COMPUTE_JOBS.c.state == state.value)
            .values(
                state=ComputeJobState.FAILED.value,
                completed_at=ensure_utc_job(now, field="now"),
                error=error,
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def aclose(self) -> None:
        await self._engine.dispose()
