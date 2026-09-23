"""`SqlVideoReadingStore` — the durable video reading cache (migration 0061).

Satisfies `nora.domain.ports.VideoReadingStore`. Never raises: a read failure
is a MISS and a write failure is swallowed, because the caller already holds a
usable reading and losing the row costs one re-read on the next ask.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.media import VideoReadingKey
from nora.infra.sql.schema import VIDEO_READINGS

log = logging.getLogger("nora.infra.sql.video_readings")


def _matches(key: VideoReadingKey) -> sa.ColumnElement[bool]:
    return sa.and_(
        VIDEO_READINGS.c.sha256 == key.sha256,
        VIDEO_READINGS.c.model == key.model,
        VIDEO_READINGS.c.chunk_seconds == key.chunk_seconds,
        VIDEO_READINGS.c.chunk_max == key.chunk_max,
        VIDEO_READINGS.c.prompt_digest == key.prompt_digest,
    )


class SqlVideoReadingStore:
    """Satisfies `nora.domain.ports.VideoReadingStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, *, key: VideoReadingKey) -> str | None:
        stmt = sa.select(VIDEO_READINGS.c.reading).where(_matches(key))
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("video_readings read failed; treating as a miss")
            return None
        if row is None or not row.reading:
            return None
        return str(row.reading)

    async def put(self, *, key: VideoReadingKey, reading: str) -> None:
        if not reading:
            # An empty reading is not a reading; storing it would turn the
            # next ask into a hit on nothing.
            return
        insert = pg_insert(VIDEO_READINGS).values(
            sha256=key.sha256, model=key.model, chunk_seconds=key.chunk_seconds,
            chunk_max=key.chunk_max, prompt_digest=key.prompt_digest,
            reading=reading,
        )
        # The same key is the same inputs, so a later reading replaces an
        # earlier one rather than raising — two rooms reading one clip at once
        # both write, and either answer is a reading of those inputs.
        stmt = insert.on_conflict_do_update(
            index_elements=[
                VIDEO_READINGS.c.sha256, VIDEO_READINGS.c.model,
                VIDEO_READINGS.c.chunk_seconds, VIDEO_READINGS.c.chunk_max,
                VIDEO_READINGS.c.prompt_digest,
            ],
            set_={"reading": insert.excluded.reading, "created_at": sa.text("now()")},
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except Exception:
            log.exception("video_readings write failed; the reading is not cached")
