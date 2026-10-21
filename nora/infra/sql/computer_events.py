"""`ComputerEventStore` over SQLAlchemy Core. Shaped like
`nora.infra.sql.tool_call_log`: insert-only. The database enforces it too
(migration `0081_computer_events`'s trigger refuses UPDATE and DELETE).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.computer_events import ComputerEvent, normalise_computer_event
from nora.infra.sql.schema import COMPUTER_EVENTS, COMPUTER_SESSIONS

_FIELDS = (
    "id", "operation_id", "phase", "created_at", "started_at", "finished_at", "actor",
    "requester_person_id",
    "platform", "conversation_id", "conversation_type", "tag_id", "scope", "tool",
    "path", "trace_id", "span_id", "status", "request", "result", "output_ref",
    "session_id",
)


def _row_to_event(r: Row[Any]) -> ComputerEvent:
    return normalise_computer_event(ComputerEvent(**{f: getattr(r, f) for f in _FIELDS}))


class SqlComputerEventStore:
    """Satisfies `nora.domain.computer_events.ComputerEventStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, event: ComputerEvent) -> None:
        row = normalise_computer_event(event)
        values = {f: getattr(row, f) for f in _FIELDS}
        for column in ("request", "result", "output_ref"):
            values[column] = dict(values[column])
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(COMPUTER_EVENTS).values(**values))

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
        platform: str | None = None,
    ) -> list[ComputerEvent]:
        where = [
            COMPUTER_EVENTS.c.conversation_id == conversation_id,
            COMPUTER_EVENTS.c.created_at >= since,
        ]
        if platform is not None:
            where.append(COMPUTER_EVENTS.c.platform == platform)
        stmt = (
            sa.select(COMPUTER_EVENTS)
            .where(*where)
            .order_by(COMPUTER_EVENTS.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def for_scope(
        self, *, scope: str, since: datetime, limit: int,
    ) -> list[ComputerEvent]:
        legacy = sa.select(COMPUTER_SESSIONS.c.scope).where(
            COMPUTER_SESSIONS.c.stopped_at_detail["workspace_scope"].astext == scope,
        )
        stmt = (sa.select(COMPUTER_EVENTS)
                .where(sa.or_(COMPUTER_EVENTS.c.scope == scope,
                              COMPUTER_EVENTS.c.scope.in_(legacy)),
                       COMPUTER_EVENTS.c.created_at >= since)
                .order_by(COMPUTER_EVENTS.c.created_at.desc()).limit(limit))
        async with self._engine.connect() as conn:
            return [_row_to_event(r) for r in (await conn.execute(stmt)).all()]

    async def for_session(self, *, session_id: UUID, limit: int) -> list[ComputerEvent]:
        stmt = (
            sa.select(COMPUTER_EVENTS)
            .where(COMPUTER_EVENTS.c.session_id == session_id)
            .order_by(COMPUTER_EVENTS.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def orphaned_starts(self, *, older_than: datetime, limit: int) -> list[ComputerEvent]:
        finished = COMPUTER_EVENTS.alias("finished")
        stmt = (
            sa.select(COMPUTER_EVENTS)
            .where(
                COMPUTER_EVENTS.c.phase == "started",
                COMPUTER_EVENTS.c.created_at < older_than,
                ~sa.exists().where(
                    finished.c.operation_id == COMPUTER_EVENTS.c.operation_id,
                    finished.c.phase == "finished",
                ),
            )
            .order_by(COMPUTER_EVENTS.c.created_at)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]
