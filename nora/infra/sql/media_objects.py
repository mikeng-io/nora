"""`MediaObjectStore` over SQLAlchemy Core — the unified media index
(DEC-138, migration 0032). One row per byte Nora holds, keyed by the content
address (sha256) of the bytes in the SAME `blob_store` (rustfs) every
acquisition path uses. `source` is an attribute (inbound/generated/fetched),
never a table.

**Never raises** (the same discipline as `SqlMediaDescriptionStore`): the
index is a bonus over delivery — a failed `save` must cost the lookup
capability, never the message the human is waiting for. Every method wraps
its own statement, logs, and degrades: a write to a no-op, a read to an
empty result.

`MediaObject` (the domain shape) lives in `nora.domain.media` beside
`MediaBlob` — the two are siblings in the same import layer, and the store
returns the domain value rather than a row proxy.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.media import MediaObject
from nora.infra.sql.schema import MEDIA_OBJECTS

log = logging.getLogger("nora.infra.sql.media_objects")


class SqlMediaObjectStore:
    """Satisfies `nora.domain.ports.MediaObjectStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, *, obj: MediaObject) -> bool:
        stmt = pg_insert(MEDIA_OBJECTS).values(
            sha256=obj.sha256,
            media_type=obj.media_type,
            size=obj.size,
            source=obj.source,
            description=obj.description,
            describe_model=obj.describe_model,
            provenance=obj.provenance or {},
            created_at_ms=obj.created_at_ms,
        ).on_conflict_do_nothing(
            index_elements=[MEDIA_OBJECTS.c.sha256],
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
            return True
        except Exception:
            log.exception("media_objects save failed; media stays deliverable")
            return False

    async def search(
        self, *, query: str, limit: int = 20,
        sources: tuple[str, ...] = (),
    ) -> tuple[MediaObject, ...]:
        """Text search over description + generated provenance text, newest
        first. Empty query → the most recent `limit` objects. Multi-word
        queries AND all words (each as a substring) — "birthday card miles"
        matches a prompt containing all three, in any order.

        `sources` filters by `source` attribute; empty means every source
        (the caller — the tool — decides what scope it may search).
        """
        words = [w for w in query.split() if w]
        conditions = []
        if words:
            # Description column OR provenance::text (which carries
            # prompt/title for generated objects).
            conditions.append(
                sa.or_(
                    *[
                        sa.or_(
                            MEDIA_OBJECTS.c.description.ilike(f"%{w}%"),
                            # provenance::text — cast, not `.astext` (which
                            # raises on SQLAlchemy 2.x JSONB columns; found
                            # live 2026-08-26 on the first container smoke).
                            sa.cast(
                                MEDIA_OBJECTS.c.provenance, sa.Text
                            ).ilike(f"%{w}%"),
                        )
                        for w in words
                    ]
                )
            )
        if sources:
            conditions.append(MEDIA_OBJECTS.c.source.in_(sources))
        stmt = sa.select(MEDIA_OBJECTS)
        if conditions:
            stmt = stmt.where(sa.and_(*conditions))
        stmt = stmt.order_by(MEDIA_OBJECTS.c.created_at_ms.desc()).limit(limit)
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        except Exception:
            log.exception("media_objects search failed; treating as empty")
            return ()
        return tuple(_row_to_object(r) for r in rows)

    async def get(self, *, sha256: str) -> MediaObject | None:
        stmt = sa.select(MEDIA_OBJECTS).where(
            MEDIA_OBJECTS.c.sha256 == sha256
        )
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("media_object get failed; treating as a miss")
            return None
        return None if row is None else _row_to_object(row)

    async def by_prefix(
        self, *, handle: str, limit: int = 20,
    ) -> tuple[MediaObject, ...]:
        """Every object whose sha256 starts with the handle. Unlike
        `media_fetch`'s conversation-scoped scan (which can see several
        blobs sharing a 6-hex prefix), this resolves against the WHOLE index
        — the caller must handle >1 result (collision) explicitly, never
        pick the first silently."""
        stmt = (
            sa.select(MEDIA_OBJECTS)
            .where(MEDIA_OBJECTS.c.sha256.like(handle + "%"))
            .order_by(MEDIA_OBJECTS.c.created_at_ms.desc())
            .limit(limit)
        )
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        except Exception:
            log.exception("media_object by_prefix failed; treating as empty")
            return ()
        return tuple(_row_to_object(r) for r in rows)


def _row_to_object(row: sa.Row[tuple[Any, ...]]) -> MediaObject:
    return MediaObject(
        sha256=row.sha256,
        media_type=row.media_type,
        size=row.size,
        source=row.source,
        description=row.description,
        provenance=dict(row.provenance or {}),
        describe_model=row.describe_model,
        created_at_ms=row.created_at_ms,
    )
