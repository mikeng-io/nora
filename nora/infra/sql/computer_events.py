"""`ComputerEventStore` over SQLAlchemy Core. Shaped like
`nora.infra.sql.tool_call_log`: insert-only, never updated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.computer_events import ComputerEvent, normalise_computer_event
from nora.infra.sql.schema import COMPUTER_EVENTS


def _row_to_event(r: Row[Any]) -> ComputerEvent:
    return normalise_computer_event(
        ComputerEvent(
            id=r.id, created_at=r.created_at, platform=r.platform,
            conversation_id=r.conversation_id, conversation_type=r.conversation_type,
            tag_id=r.tag_id, scope=r.scope, tool=r.tool, status=r.status,
            exit_code=r.exit_code, duration_ms=r.duration_ms, detail=r.detail,
        )
    )


class SqlComputerEventStore:
    """Satisfies `nora.domain.computer_events.ComputerEventStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, event: ComputerEvent) -> None:
        row = normalise_computer_event(event)
        stmt = sa.insert(COMPUTER_EVENTS).values(
            id=row.id, created_at=row.created_at, platform=row.platform,
            conversation_id=row.conversation_id,
            conversation_type=row.conversation_type, tag_id=row.tag_id,
            scope=row.scope, tool=row.tool, status=row.status,
            exit_code=row.exit_code, duration_ms=row.duration_ms,
            detail=dict(row.detail or {}),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
    ) -> list[ComputerEvent]:
        stmt = (
            sa.select(COMPUTER_EVENTS)
            .where(
                COMPUTER_EVENTS.c.conversation_id == conversation_id,
                COMPUTER_EVENTS.c.created_at >= since,
            )
            .order_by(COMPUTER_EVENTS.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]
