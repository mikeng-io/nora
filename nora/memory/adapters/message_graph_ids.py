"""Look up registered graph IDs for messages already selected by a pass."""

from collections.abc import Collection

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema import MESSAGES
from nora.memory.domain.retrieval import ReadingRoom


class SqlMessageGraphIds:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(
        self, room: ReadingRoom, unit: str, ids: Collection[int],
    ) -> dict[int, str]:
        if not unit or not ids:
            return {}
        m = MESSAGES.c
        async with self._engine.connect() as conn:
            rows = await conn.execute(sa.select(m.id, m.graph_id).where(
                m.id.in_(ids), m.platform == room.platform,
                m.conversation_id == room.conversation_id,
                m.unit == unit, m.state == "live", m.exposure != "operator",
                m.graph_id.is_not(None),
            ))
            return {row.id: row.graph_id for row in rows}
