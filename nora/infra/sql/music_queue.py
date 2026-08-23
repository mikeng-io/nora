"""`SqlMusicQueueStore` over SQLAlchemy Core (design/discord-music/
03-state-and-persistence.md). Shaped like `nora.infra.sql.reminders`: the
engine comes through `__init__`, every write is one statement inside
`async with self._engine.begin()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.music import QueuedTrack, ensure_utc
from nora.infra.sql.schema import MUSIC_QUEUE


def _row_to_track(row: Row[Any]) -> QueuedTrack:
    return QueuedTrack(
        id=row.id, guild_id=row.guild_id, position=row.position, source=row.source,
        url=row.url, title=row.title, added_by_person_id=row.added_by_person_id,
        added_at=row.added_at,
    )


class SqlMusicQueueStore:
    """Satisfies `nora.domain.music.MusicQueueStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def enqueue(
        self,
        *,
        guild_id: str,
        source: str,
        url: str,
        title: str,
        added_by_person_id: str,
        added_at: datetime,
    ) -> QueuedTrack:
        # The position subquery and the INSERT are one statement, so two
        # `/music play` calls landing for the same guild at once cannot both
        # read the same max and collide on one position — a read-then-write
        # in Python would race exactly there.
        next_position = (
            sa.select(sa.func.coalesce(sa.func.max(MUSIC_QUEUE.c.position) + 1, 0))
            .where(MUSIC_QUEUE.c.guild_id == guild_id)
            .scalar_subquery()
        )
        stmt = (
            sa.insert(MUSIC_QUEUE)
            .values(
                id=uuid4(),
                guild_id=guild_id,
                position=next_position,
                source=source,
                url=url,
                title=title,
                added_by_person_id=added_by_person_id,
                added_at=ensure_utc(added_at, field="added_at"),
            )
            .returning(*MUSIC_QUEUE.c)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one()
        return _row_to_track(row)

    async def pop_next(self, guild_id: str) -> QueuedTrack | None:
        # `ORDER BY position LIMIT 1` scoped inside the DELETE's own subquery
        # (`FOR UPDATE SKIP LOCKED` is not needed here — Nora runs one
        # process, so there is no second connection to skip past) is what
        # makes "which row" and "delete it" atomic: two concurrent callers
        # (a `/music skip` racing a track finishing naturally) cannot both
        # delete-and-return the same row.
        next_id = (
            sa.select(MUSIC_QUEUE.c.id)
            .where(MUSIC_QUEUE.c.guild_id == guild_id)
            .order_by(MUSIC_QUEUE.c.position.asc())
            .limit(1)
        )
        stmt = (
            sa.delete(MUSIC_QUEUE)
            .where(MUSIC_QUEUE.c.id.in_(next_id))
            .returning(*MUSIC_QUEUE.c)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_track(row)

    async def queue_for(self, guild_id: str) -> Sequence[QueuedTrack]:
        stmt = (
            sa.select(MUSIC_QUEUE)
            .where(MUSIC_QUEUE.c.guild_id == guild_id)
            .order_by(MUSIC_QUEUE.c.position.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_track(row) for row in rows]

    async def clear(self, guild_id: str) -> int:
        stmt = sa.delete(MUSIC_QUEUE).where(MUSIC_QUEUE.c.guild_id == guild_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def aclose(self) -> None:
        await self._engine.dispose()
