"""`SqlMusicQueueStore` over SQLAlchemy Core. Shaped like `nora.infra.sql.reminders`: the
engine comes through `__init__`, every write is one statement inside
`async with self._engine.begin()`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.music import QueuedTrack, ensure_utc
from nora.infra.sql.schema import MUSIC_QUEUE

log = logging.getLogger("nora.music")

# Postgres's own SQLSTATE for a unique-constraint violation — same constant
# `nora.infra.sql.reminders.SqlReminderStore.create` uses to translate its
# own `IntegrityError`.
_PG_UNIQUE_VIOLATION = "23505"

# One or two collisions is the realistic case (a couple of `/music play`
# calls landing at the same instant), but a genuine thundering herd (many
# concurrent callers all retrying into each other) needs real headroom:
# concurrent enqueues for one guild can need several dozen combined retries
# to all land distinct positions. The bound exists so a genuinely corrupted table (e.g. a
# manually-inserted row) fails loudly instead of retrying forever, not to
# cap ordinary contention.
_MAX_ENQUEUE_ATTEMPTS = 20


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
        # The position subquery and the INSERT are one statement, which
        # keeps two concurrent `/music play` calls from racing on it in
        # PYTHON — but Postgres's default READ COMMITTED isolation does NOT
        # make the subquery-then-insert atomic ACROSS two transactions: both
        # can read the same MAX and both attempt the same position. That is
        # exactly what `uq_music_queue_guild_position` (migration 0028)
        # exists to catch — retried here as a decidable `IntegrityError`,
        # never a silently-duplicated position.
        added_at_utc = ensure_utc(added_at, field="added_at")
        last_error: IntegrityError | None = None
        for _attempt in range(_MAX_ENQUEUE_ATTEMPTS):
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
                    added_at=added_at_utc,
                )
                .returning(*MUSIC_QUEUE.c)
            )
            try:
                async with self._engine.begin() as conn:
                    row = (await conn.execute(stmt)).one()
            except IntegrityError as exc:
                if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                    raise
                log.info(
                    "enqueue position collision for guild %s, retrying (attempt %d)",
                    guild_id, _attempt + 1,
                )
                last_error = exc
                continue
            return _row_to_track(row)
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable: the loop above always sets last_error before exiting")

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

    async def move_to_front(self, guild_id: str, *, position: int) -> QueuedTrack | None:
        # A direct swap of two rows' `position` values would transiently
        # violate `uq_music_queue_guild_position` mid-statement (Postgres
        # checks the unique index per row, not once for the whole UPDATE) --
        # so this shifts every row for the guild into a range no ordinary
        # queue ever reaches first, then assigns final 0..N-1 positions one
        # row at a time. Both phases run inside one transaction, so a
        # concurrent `queue_for`/`pop_next` never observes the shifted,
        # not-yet-final state.
        shift = 1_000_000
        async with self._engine.begin() as conn:
            rows = (
                await conn.execute(
                    sa.select(MUSIC_QUEUE)
                    .where(MUSIC_QUEUE.c.guild_id == guild_id)
                    .order_by(MUSIC_QUEUE.c.position.asc())
                )
            ).all()
            tracks = [_row_to_track(row) for row in rows]
            if position < 1 or position > len(tracks):
                return None
            target = tracks[position - 1]
            if position == 1:
                return target
            reordered = [target] + [t for t in tracks if t.id != target.id]
            await conn.execute(
                sa.update(MUSIC_QUEUE)
                .where(MUSIC_QUEUE.c.guild_id == guild_id)
                .values(position=MUSIC_QUEUE.c.position + shift)
            )
            for new_position, track in enumerate(reordered):
                await conn.execute(
                    sa.update(MUSIC_QUEUE)
                    .where(MUSIC_QUEUE.c.id == track.id)
                    .values(position=new_position)
                )
        return QueuedTrack(
            id=target.id, guild_id=target.guild_id, position=0, source=target.source,
            url=target.url, title=target.title, added_by_person_id=target.added_by_person_id,
            added_at=target.added_at,
        )

    async def aclose(self) -> None:
        await self._engine.dispose()
