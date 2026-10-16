"""`MediaDescriptionStore` over SQLAlchemy Core — the two-level media dedup
cache, rooted on `media_objects` (DEC-138). Shaped
like `nora.infra.sql.tool_call_log`, plus upserts, because a sticker is seen
many times.

**Never raises.** The port says so and the ingest path depends on it: a cache
failure must cost one re-description, never the message. Every method wraps
its own statement, logs, and degrades — a read to a miss, a write to a no-op.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.media import MediaBlob
from nora.infra.sql import memory_index
from nora.infra.sql.schema import MEDIA_OBJECTS, STICKER_INDEX

log = logging.getLogger("nora.infra.sql.media_descriptions")


class SqlMediaDescriptionStore:
    """Satisfies `nora.domain.ports.MediaDescriptionStore`."""

    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        """`metrics` is injected for `SqlRecordStore.__init__`'s reason; it
        counts the memory index's projection of a description."""
        self._engine = engine
        self._metrics = metrics

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
        stmt = sa.select(MEDIA_OBJECTS.c.description).where(
            MEDIA_OBJECTS.c.sha256 == sha256
        )
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("media_objects read failed; treating as a miss")
            return None
        if row is None or not row.description:
            # EMPTY description is a MISS, not a hit. The DEC-138 M4 inbound
            # write (ingest/stages/media.py) gives every byte Nora takes
            # custody of a `description=""` row BEFORE any vision model has
            # run — the row is the index entry, not a description. Serving
            # that placeholder as a cached description made every first
            # `media_fetch` of an undescribed medium return ok+chars=0 and
            # NEVER call the describer. Empty/None must reach the caller as
            # None so `VisionCache.aget`/`media_fetch` treat it as a miss
            # and actually describe the bytes.
            return None
        return str(row.description)

    async def remember(
        self, *, platform: str, token: str, blob: MediaBlob,
        description: str, model: str,
    ) -> None:
        """Both levels in ONE transaction, so a crash between them cannot leave
        an identity pointing at a hash with no description — the state that
        would make every later sighting re-describe while looking like a hit.

        `token=""` means "no sticker identity" (`VisionCache.aput`, called from
        `media_fetch`: an image a human pointed at by handle has only content,
        never a platform-side identity). The `sticker_index` write is guarded
        on a non-empty token for that case — every `media_fetch` write shares
        `platform=""`/`token=""`, and without the guard they would all upsert
        the SAME junk row, each one clobbering the last hash it wrote. The
        description row is written regardless: content-level dedup is the
        whole point even when there is no identity to index.

        `description=""` means "index this identity, describe nothing" — the
        shape `StickerReader` uses, because the model pass runs in the spawned
        comprehension job, off the ingest path. It must NOT clobber
        a description already on the row: the empty string is what every
        un-comprehended row already carries (`describe` reads it as a MISS),
        so an unguarded overwrite would silently erase a real comprehension
        every time the same sticker was seen again. `nullif`+`coalesce` is what
        makes the write index-only in that case.
        """
        desc_insert = pg_insert(MEDIA_OBJECTS).values(
            sha256=blob.sha256, media_type=blob.media_type, size=blob.size,
            source="inbound", description=description, describe_model=model or None,
            provenance={}, created_at_ms=0,
        )
        desc_stmt = desc_insert.on_conflict_do_update(
            index_elements=[MEDIA_OBJECTS.c.sha256],
            # Description is a pure function of the bytes: a later describer
            # may refresh it (DEC-138). Provenance is NEVER overwritten — the
            # first source to hold the bytes wins. `source` stays as first
            # seen; a sticker described as 'inbound' is inbound even if the
            # same art was later generated. An EMPTY incoming description
            # keeps whatever is there — see the docstring.
            set_={
                "description": sa.func.coalesce(
                    sa.func.nullif(desc_insert.excluded.description, ""),
                    MEDIA_OBJECTS.c.description,
                ),
                # Moves WITH the description or not at all: a row whose
                # description was kept must keep the model that wrote it, or
                # the provenance of the text on the row would name a describer
                # that never produced it.
                "describe_model": sa.case(
                    (
                        desc_insert.excluded.description != "",
                        desc_insert.excluded.describe_model,
                    ),
                    else_=MEDIA_OBJECTS.c.describe_model,
                ),
            },
        )
        try:
            async with self._engine.begin() as conn:
                if token:
                    index_stmt = pg_insert(STICKER_INDEX).values(
                        platform=platform, fetch_token=token, sha256=blob.sha256,
                        media_type=blob.media_type, size=blob.size, hits=1,
                    )
                    index_stmt = index_stmt.on_conflict_do_update(
                        index_elements=[STICKER_INDEX.c.platform, STICKER_INDEX.c.fetch_token],
                        # `sha256`/`media_type`/`size` are updated TOGETHER,
                        # deliberately: they are one indivisible fact about
                        # the held blob (`MediaBlob`), so a future column
                        # derived from the blob (e.g. a dimension, a
                        # duration) belongs in this same set, never added to
                        # only one of the three and left to drift from the
                        # others on a re-upsert.
                        set_={
                            "sha256": index_stmt.excluded.sha256,
                            "media_type": index_stmt.excluded.media_type,
                            "size": index_stmt.excluded.size,
                            "last_seen": sa.text("now()"),
                            "hits": STICKER_INDEX.c.hits + 1,
                        },
                    )
                    await conn.execute(index_stmt)
                await conn.execute(desc_stmt)
                # The description is an attachment's reading.
                await memory_index.reproject_media(
                    conn, blob.sha256, metrics=self._metrics,
                )
        except Exception:
            # The caller already has a usable description and nothing to do
            # differently. Losing the cache row costs one re-description on the
            # next sighting; raising here would cost the message.
            log.exception("media dedup write failed; the description is not cached")
