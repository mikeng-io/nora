"""`MediaDescriptionStore` over SQLAlchemy Core — the two-level media dedup
cache (sticker spec §4.1). Shaped like `nora.infra.sql.tool_call_log`, plus
upserts, because a sticker is seen many times.

**Never raises.** The port says so and the ingest path depends on it: a cache
failure must cost one re-description, never the message. Every method wraps its
own statement, logs, and degrades — a read to a miss, a write to a no-op.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.media import MediaBlob
from nora.infra.sql.schema import MEDIA_DESCRIPTIONS, STICKER_INDEX

log = logging.getLogger("nora.infra.sql.media_descriptions")


class SqlMediaDescriptionStore:
    """Satisfies `nora.domain.ports.MediaDescriptionStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def resolve(self, *, platform: str, token: str) -> MediaBlob | None:
        stmt = sa.select(
            STICKER_INDEX.c.sha256, STICKER_INDEX.c.media_type, STICKER_INDEX.c.size,
        ).where(
            STICKER_INDEX.c.platform == platform,
            STICKER_INDEX.c.fetch_token == token,
        )
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("sticker_index read failed; treating as a miss")
            return None
        if row is None:
            return None
        return MediaBlob(sha256=row.sha256, media_type=row.media_type, size=row.size)

    async def describe(self, *, sha256: str) -> str | None:
        stmt = sa.select(MEDIA_DESCRIPTIONS.c.description).where(
            MEDIA_DESCRIPTIONS.c.sha256 == sha256
        )
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("media_descriptions read failed; treating as a miss")
            return None
        return None if row is None else str(row.description)

    async def remember(
        self, *, platform: str, token: str, blob: MediaBlob,
        description: str, model: str,
    ) -> None:
        """Both levels in ONE transaction, so a crash between them cannot leave
        an identity pointing at a hash with no description — the state that
        would make every later sighting re-describe while looking like a hit.
        """
        index_stmt = pg_insert(STICKER_INDEX).values(
            platform=platform, fetch_token=token, sha256=blob.sha256,
            media_type=blob.media_type, size=blob.size, hits=1,
        )
        index_stmt = index_stmt.on_conflict_do_update(
            index_elements=[STICKER_INDEX.c.platform, STICKER_INDEX.c.fetch_token],
            set_={
                "sha256": index_stmt.excluded.sha256,
                "media_type": index_stmt.excluded.media_type,
                "size": index_stmt.excluded.size,
                "last_seen": sa.text("now()"),
                "hits": STICKER_INDEX.c.hits + 1,
            },
        )
        desc_stmt = pg_insert(MEDIA_DESCRIPTIONS).values(
            sha256=blob.sha256, description=description, model=model,
        ).on_conflict_do_nothing(index_elements=[MEDIA_DESCRIPTIONS.c.sha256])
        try:
            async with self._engine.begin() as conn:
                await conn.execute(index_stmt)
                await conn.execute(desc_stmt)
        except Exception:
            # The caller already has a usable description and nothing to do
            # differently. Losing the cache row costs one re-description on the
            # next sighting; raising here would cost the message.
            log.exception("media dedup write failed; the description is not cached")
