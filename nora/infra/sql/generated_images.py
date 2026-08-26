"""`GeneratedImageStore` over SQLAlchemy Core — Nora's generated-image
library (migration 0031). One row per successful `generate_image` call,
keyed by the content address (sha256) of the bytes in the SAME `blob_store`
(rustfs) the inbound media pipeline uses.

**Never raises** (the same discipline as `SqlMediaDescriptionStore`): the
library is a bonus over attachment delivery — a failed `save` must cost the
lookup capability, never the message the human is waiting for. Every method
wraps its own statement, logs, and degrades: a write to a no-op, a read to
an empty result.

`GeneratedImage` (the domain shape) lives in `nora.domain.media` beside
`MediaBlob` — the two are siblings in the same import layer, and the store
returns the domain value rather than a row proxy.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.media import GeneratedImage
from nora.infra.sql.schema import GENERATED_IMAGES

log = logging.getLogger("nora.infra.sql.generated_images")


class SqlGeneratedImageStore:
    """Satisfies `nora.domain.ports.GeneratedImageStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, *, image: GeneratedImage) -> bool:
        stmt = pg_insert(GENERATED_IMAGES).values(
            sha256=image.sha256,
            prompt=image.prompt,
            title=image.title,
            description=image.description,
            provider=image.provider,
            model=image.model,
            aspect_ratio=image.aspect_ratio,
            media_type=image.media_type,
            size=image.size,
            platform=image.platform,
            conversation_id=image.conversation_id,
            created_at_ms=image.created_at_ms,
        ).on_conflict_do_nothing(
            index_elements=[GENERATED_IMAGES.c.sha256],
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
            return True
        except Exception:
            log.exception("generated_images save failed; image stays attachable")
            return False

    async def search(self, *, query: str, limit: int = 20) -> tuple[GeneratedImage, ...]:
        """Text search over prompt/title/description, newest first. Empty
        query → the most recent `limit` images. Multi-word queries AND all
        words (each as a substring) — "birthday card miles" matches a prompt
        containing all three, in any order."""
        words = [w for w in query.split() if w]
        if not words:
            stmt = (
                sa.select(GENERATED_IMAGES)
                .order_by(GENERATED_IMAGES.c.created_at_ms.desc())
                .limit(limit)
            )
        else:
            clauses = sa.or_(
                *[
                    sa.or_(
                        GENERATED_IMAGES.c.prompt.ilike(f"%{w}%"),
                        GENERATED_IMAGES.c.title.ilike(f"%{w}%"),
                        GENERATED_IMAGES.c.description.ilike(f"%{w}%"),
                    )
                    for w in words
                ]
            )
            stmt = (
                sa.select(GENERATED_IMAGES)
                .where(clauses)
                .order_by(GENERATED_IMAGES.c.created_at_ms.desc())
                .limit(limit)
            )
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        except Exception:
            log.exception("generated_image search failed; treating as empty")
            return ()
        return tuple(_row_to_image(r) for r in rows)

    async def get(self, *, sha256: str) -> GeneratedImage | None:
        stmt = sa.select(GENERATED_IMAGES).where(
            GENERATED_IMAGES.c.sha256 == sha256
        )
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("generated_image get failed; treating as a miss")
            return None
        return None if row is None else _row_to_image(row)


def _row_to_image(row: sa.Row[tuple[Any, ...]]) -> GeneratedImage:
    return GeneratedImage(
        sha256=row.sha256,
        prompt=row.prompt,
        title=row.title,
        description=row.description,
        provider=row.provider,
        model=row.model,
        aspect_ratio=row.aspect_ratio,
        media_type=row.media_type,
        size=row.size,
        platform=row.platform,
        conversation_id=row.conversation_id,
        created_at_ms=row.created_at_ms,
    )
