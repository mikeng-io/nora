"""`SqlMusicSessionStore` over SQLAlchemy Core. Shaped like `nora.infra.sql.music_queue`: the
engine comes through `__init__`, every write is one statement inside
`async with self._engine.begin()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.music import MusicSession, ensure_utc
from nora.infra.sql.schema import MUSIC_SESSION


def _row_to_session(row: Row[Any]) -> MusicSession:
    return MusicSession(
        guild_id=row.guild_id, voice_channel_id=row.voice_channel_id,
        notify_channel_id=row.notify_channel_id, now_playing_source=row.now_playing_source,
        now_playing_locator=row.now_playing_locator, now_playing_title=row.now_playing_title,
        paused=row.paused, volume=row.volume, updated_at=row.updated_at,
        last_played_source=row.last_played_source, last_played_locator=row.last_played_locator,
        last_played_title=row.last_played_title,
    )


class SqlMusicSessionStore:
    """Satisfies `nora.domain.music.MusicSessionStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def upsert(self, session: MusicSession) -> None:
        updated_at = ensure_utc(session.updated_at, field="updated_at")
        values = {
            "guild_id": session.guild_id,
            "voice_channel_id": session.voice_channel_id,
            "notify_channel_id": session.notify_channel_id,
            "now_playing_source": session.now_playing_source,
            "now_playing_locator": session.now_playing_locator,
            "now_playing_title": session.now_playing_title,
            "last_played_source": session.last_played_source,
            "last_played_locator": session.last_played_locator,
            "last_played_title": session.last_played_title,
            "paused": session.paused,
            "volume": session.volume,
            "updated_at": updated_at,
        }
        stmt = pg_insert(MUSIC_SESSION).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MUSIC_SESSION.c.guild_id],
            set_={k: v for k, v in values.items() if k != "guild_id"},
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, guild_id: str) -> MusicSession | None:
        stmt = sa.select(MUSIC_SESSION).where(MUSIC_SESSION.c.guild_id == guild_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_session(row)

    async def all(self) -> Sequence[MusicSession]:
        stmt = sa.select(MUSIC_SESSION)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_session(row) for row in rows]

    async def delete(self, guild_id: str) -> None:
        stmt = sa.delete(MUSIC_SESSION).where(MUSIC_SESSION.c.guild_id == guild_id)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def aclose(self) -> None:
        await self._engine.dispose()
