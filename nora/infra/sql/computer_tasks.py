"""`ComputerTaskStore` over SQLAlchemy Core (`computer_tasks`, migration
`0147`; `nora.domain.computer_tasks`).

`claim` is the compare-and-set that admits a queued segment to the scope's
one running slot; `finish` persists the terminal result before the stream
entry is acked; `request_cancel` takes a queued row to `cancelled` or flags a
running one. Nothing deletes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.computer_tasks import (
    CANCELLED,
    INTERRUPTED,
    QUEUED,
    RUNNING,
    TERMINAL,
    ComputerTask,
    ensure_utc_event,
    normalise_task,
)
from nora.infra.sql.schema import COMPUTER_TASKS as T

_FIELDS = (
    "id", "scope", "kind", "status", "payload", "origin", "result", "parent",
    "created_at", "updated_at", "started_at", "finished_at", "deadline_at",
    "cancel_requested_at", "delivered_at",
)


def _row(r: Row[*tuple[Any, ...]]) -> ComputerTask:
    return normalise_task(ComputerTask(**{f: getattr(r, f) for f in _FIELDS}))


class SqlComputerTaskStore:
    """Satisfies `nora.domain.computer_tasks.ComputerTaskStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def enqueue(self, task: ComputerTask) -> ComputerTask:
        task = normalise_task(task)
        async with self._engine.begin() as conn:
            await conn.execute(T.insert().values(
                **{f: getattr(task, f) for f in _FIELDS}))
        return task

    async def get(self, task_id: UUID) -> ComputerTask | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(T).where(T.c.id == task_id))).first()
        return _row(row) if row is not None else None

    async def claim(self, task_id: UUID, *, now: datetime) -> ComputerTask | None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.status == QUEUED)
                .values(status=RUNNING, started_at=now, updated_at=now)
                .returning(T)
            )).first()
        return _row(row) if row is not None else None

    async def try_claim_head(self, task_id: UUID, *, now: datetime) -> ComputerTask | None:
        """Claim ONLY if this row may run now: still `queued`, the scope's
        OLDEST `queued` row, and NO row of its scope `running`. The running
        exclusion is a separate predicate, not folded into the head pick —
        picking the oldest open row lets a late-committed older `queued` row
        start while another segment is already mid-flight.

        The exclusion is serialized on the scope itself: `pg_advisory_xact_
        lock` on the scope key, taken INSIDE this transaction before the
        update. A `NOT EXISTS` on the statement snapshot alone cannot
        exclude a concurrent grant — two claims each miss the other's
        uncommitted `running` row; the lock makes the second claimer's
        update see the first's committed row (each statement re-reads
        under READ COMMITTED)."""
        now = ensure_utc_event(now, field="now")
        scope = sa.select(T.c.scope).where(T.c.id == task_id).scalar_subquery()
        head = sa.select(T.c.id).where(
            T.c.scope == scope, T.c.status == QUEUED,
        ).order_by(T.c.created_at, T.c.id).limit(1).scalar_subquery()
        running = sa.select(T.c.id).where(
            T.c.scope == scope, T.c.status == RUNNING)
        async with self._engine.begin() as conn:
            owner = (await conn.execute(
                sa.select(T.c.scope).where(T.c.id == task_id))).scalar()
            if owner is None:
                return None
            await conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": str(owner)})
            row = (await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.status == QUEUED,
                       T.c.id == head, ~sa.exists(running))
                .values(status=RUNNING, started_at=now, updated_at=now)
                .returning(T)
            )).first()
        return _row(row) if row is not None else None

    async def finish(self, task_id: UUID, *, status: str,
                     result: dict[str, Any], now: datetime) -> None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.status == RUNNING)
                .values(status=status, result=result,
                        finished_at=now, updated_at=now)
            )

    async def request_cancel(self, task_id: UUID, *, now: datetime) -> ComputerTask | None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.status.in_((QUEUED, RUNNING)))
                .values(
                    status=sa.case(
                        (T.c.status == QUEUED, CANCELLED),
                        else_=RUNNING),
                    cancel_requested_at=sa.func.coalesce(
                        T.c.cancel_requested_at, now),
                    finished_at=sa.case(
                        (T.c.status == QUEUED, now), else_=T.c.finished_at),
                    updated_at=now)
                .returning(T)
            )).first()
            if row is None:
                row = (await conn.execute(
                    sa.select(T).where(T.c.id == task_id))).first()
        return _row(row) if row is not None else None

    async def unannounced(self, *, limit: int) -> list[ComputerTask]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T).where(T.c.status == QUEUED)
                .order_by(T.c.created_at).limit(limit))).all()
        return [_row(r) for r in rows]

    async def interrupted(self, *, older_than: datetime, limit: int) -> list[ComputerTask]:
        older_than = ensure_utc_event(older_than, field="older_than")
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T).where(
                    T.c.status == RUNNING, T.c.started_at < older_than)
                .order_by(T.c.started_at).limit(limit))).all()
        return [_row(r) for r in rows]

    async def mark_interrupted(self, task_id: UUID, *, now: datetime,
                             reason: str) -> None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.status == RUNNING)
                .values(status=INTERRUPTED, result={"reason": reason},
                        finished_at=now, updated_at=now)
            )

    async def open_for_scope(self, scope: str, *, limit: int) -> list[ComputerTask]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T).where(
                    T.c.scope == scope,
                    T.c.status.in_((QUEUED, RUNNING)))
                .order_by(T.c.created_at).limit(limit))).all()
        return [_row(r) for r in rows]

    async def open_scopes(self, *, limit: int) -> list[str]:
        """The scopes with any open row: the runner's breadth read, from the
        table itself — the record of truth — never a Redis SCAN."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T.c.scope).where(
                    T.c.status.in_((QUEUED, RUNNING)))
                .distinct().limit(limit))).all()
        return [str(r.scope) for r in rows]

    async def mark_delivered(self, task_id: UUID, *, now: datetime) -> None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(T).where(T.c.id == task_id)
                .values(delivered_at=now, updated_at=now))

    async def elect_delivery(self, task_id: UUID, *, now: datetime,
                             job: Any = None) -> ComputerTask | None:
        """One transaction: stamp `delivered_at` where still unset (and the
        row is terminal), then — when `job` rides along — file the
        `compute_jobs` outbox row in the same commit. The live handoff and
        the deferred path both elect; exactly one wins."""
        from nora.infra.sql.compute_jobs import _job_values
        from nora.infra.sql.schema import COMPUTE_JOBS

        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.delivered_at.is_(None),
                       T.c.status.in_(tuple(sorted(TERMINAL))))
                .values(delivered_at=now, updated_at=now)
                .returning(T)
            )).first()
            if row is None:
                return None
            if job is not None:
                await conn.execute(
                    sa.insert(COMPUTE_JOBS).values(**_job_values(job)))
        return _row(row)

    async def undelivered(self, *, limit: int) -> list[ComputerTask]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(T).where(
                    T.c.status.in_(tuple(sorted(TERMINAL))),
                    T.c.delivered_at.is_(None))
                .order_by(T.c.finished_at).limit(limit))).all()
        return [_row(r) for r in rows]
