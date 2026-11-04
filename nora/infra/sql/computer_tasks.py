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


def _row(r: Row[Any]) -> ComputerTask:
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
        now = ensure_utc_event(now, field="now")
        head = sa.select(T.c.id).where(
            T.c.scope == sa.select(T.c.scope).where(
                T.c.id == task_id).scalar_subquery(),
            T.c.status.in_((QUEUED, RUNNING)),
        ).order_by(T.c.created_at, T.c.id).limit(1).scalar_subquery()
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.update(T)
                .where(T.c.id == task_id, T.c.status == QUEUED,
                       T.c.id == head)
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

    async def mark_delivered(self, task_id: UUID, *, now: datetime) -> None:
        now = ensure_utc_event(now, field="now")
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(T).where(T.c.id == task_id)
                .values(delivered_at=now, updated_at=now))
