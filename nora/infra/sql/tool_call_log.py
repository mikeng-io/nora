"""`ToolCallLogStore` over SQLAlchemy Core. Shaped like
`nora.infra.sql.compute_jobs`, minus the state machine — a log entry is
insert-only and never updated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.tool_call_log import ToolCallLogEntry, normalise_tool_call_log_entry
from nora.infra.sql.schema import TOOL_CALL_LOG


def _row_to_entry(r: Row[Any]) -> ToolCallLogEntry:
    return normalise_tool_call_log_entry(
        ToolCallLogEntry(
            id=r.id, conversation_id=r.conversation_id,
            conversation_type=r.conversation_type, tag_id=r.tag_id,
            tool_name=r.tool_name, tool_input=r.tool_input, tool_result=r.tool_result,
            created_at=r.created_at,
        )
    )


class SqlToolCallLogStore:
    """Satisfies `nora.domain.tool_call_log.ToolCallLogStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, entry: ToolCallLogEntry) -> None:
        row = normalise_tool_call_log_entry(entry)
        stmt = sa.insert(TOOL_CALL_LOG).values(
            id=row.id, conversation_id=row.conversation_id,
            conversation_type=row.conversation_type, tag_id=row.tag_id,
            tool_name=row.tool_name, tool_input=row.tool_input,
            tool_result=row.tool_result, created_at=row.created_at,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def recent(
        self, *, conversation_id: str, since: datetime, limit: int,
    ) -> list[ToolCallLogEntry]:
        stmt = (
            sa.select(TOOL_CALL_LOG)
            .where(
                TOOL_CALL_LOG.c.conversation_id == conversation_id,
                TOOL_CALL_LOG.c.created_at >= since,
            )
            .order_by(TOOL_CALL_LOG.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_entry(r) for r in rows]
