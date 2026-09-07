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
        insert = pg_insert(MEDIA_OBJECTS).values(
            sha256=obj.sha256,
            media_type=obj.media_type,
            size=obj.size,
            source=obj.source,
            title=obj.title,
            description=obj.description,
            content=obj.content,
            describe_model=obj.describe_model,
            provenance=obj.provenance or {},
            created_at_ms=obj.created_at_ms,
        )
        stmt = insert.on_conflict_do_update(
            index_elements=[MEDIA_OBJECTS.c.sha256],
            # STILL first-source-wins for a real row — the `where` is what
            # keeps that true. It repairs exactly one thing: the SHELL
            # `comprehend` inserts when the spawned read beats the inline
            # custody write (`media_type=''`, `size=0`, `created_at_ms=0`,
            # empty provenance). Under a plain `on_conflict_do_nothing` custody
            # then had nothing to update and the row stayed a shell forever —
            # no media type for `media_fetch`'s modality gate, no size, no
            # birth record, and `search`'s `created_at_ms DESC` ordering putting
            # it last for all time. The comprehension FIELDS are untouched
            # here: a read that already landed is never rolled back by the
            # custody write catching up.
            set_={
                "media_type": insert.excluded.media_type,
                "size": insert.excluded.size,
                "source": insert.excluded.source,
                # MERGED, not replaced: on the race path the shell already
                # carries the READ's chunk record, and overwriting it with the
                # birth record would throw away the "recorded" half of DEC-161
                # exactly when the read finished first. Birth keys win a
                # collision; the read's keys survive beside them.
                "provenance": MEDIA_OBJECTS.c.provenance.op("||")(
                    insert.excluded.provenance
                ),
                "created_at_ms": insert.excluded.created_at_ms,
            },
            where=sa.and_(
                MEDIA_OBJECTS.c.media_type == "",
                MEDIA_OBJECTS.c.created_at_ms == 0,
            ),
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
            return True
        except Exception:
            log.exception("media_objects save failed; media stays deliverable")
            return False

    async def comprehend(
        self, *, sha256: str, title: str, description: str, content: str,
        model: str, provenance: dict[str, object] | None = None,
    ) -> bool:
        """Land one model pass's three fields on the row for `sha256`.

        An UPSERT rather than an UPDATE, because the two writers race by
        design: custody writes the index entry inline and the comprehension job
        is SPAWNED (issue #116), so a fast model on a slow disk can arrive
        first. `source` is `'inbound'` on the insert arm for the same reason
        `SqlMediaDescriptionStore.remember` uses it — a comprehension only ever
        runs on bytes that arrived — and on the conflict arm `source` and
        `created_at_ms` are NOT in the SET at all: the birth record belongs to
        whoever first held the bytes and is never overwritten (DEC-138).

        `provenance` MERGES (`jsonb ||`) rather than replacing. What a READ
        leaves behind — a video's chunk record (issue #117): the clip's length,
        how much of it reached a model, whether the chunk ceiling is why the
        rest did not — is a fact about the READING, not about the birth, and a
        generated image's prompt/provider/aspect_ratio has to survive beside
        it. `||` is right-biased at the top level, so a re-read updates its own
        keys and touches no others.

        **Never raises**, like every other method here. A lost comprehension
        costs one re-read on the next sighting; it must never cost the message,
        and this runs in a spawned task where a raise would surface only as a
        done-callback log line.
        """
        insert = pg_insert(MEDIA_OBJECTS).values(
            sha256=sha256, media_type="", size=0, source="inbound",
            title=title, description=description, content=content,
            describe_model=model or None, provenance=provenance or {},
            created_at_ms=0,
        )
        stmt = insert.on_conflict_do_update(
            index_elements=[MEDIA_OBJECTS.c.sha256],
            set_={
                "title": insert.excluded.title,
                "description": insert.excluded.description,
                "content": insert.excluded.content,
                "describe_model": insert.excluded.describe_model,
                "provenance": MEDIA_OBJECTS.c.provenance.op("||")(
                    insert.excluded.provenance
                ),
            },
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
            return True
        except Exception:
            log.exception("media_objects comprehend failed; the read is not cached")
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
            # EVERY word must match (AND across words), each against the
            # description column OR provenance::text (which carries
            # prompt/title for generated objects). This was `sa.or_` across
            # words until 2026-09-07 — "birthday card miles" returned every
            # birthday card — and nothing noticed because the port had no
            # contract suite and each test file's fake invented its own
            # search; `tests/contract/test_media_object_store_contract.py`
            # is what turned it red.
            conditions.append(
                sa.and_(
                    *[
                        sa.or_(
                            # All three comprehension fields (issue #116), not
                            # description alone: one model pass writes the
                            # title and the legible content too, and an index
                            # that could not find «the whiteboard that said
                            # Q3» would be holding the answer and refusing to
                            # look at it. `FakeMediaObjectStore` matches the
                            # same three.
                            MEDIA_OBJECTS.c.title.ilike(f"%{w}%"),
                            MEDIA_OBJECTS.c.content.ilike(f"%{w}%"),
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
        self, *, handle: str, limit: int = 100,
    ) -> tuple[MediaObject, ...]:
        """Every object whose sha256 starts with the handle. Unlike
        `media_fetch`'s conversation-scoped scan (which can see several
        blobs sharing a 6-hex prefix), this resolves against the WHOLE index
        — the caller must handle >1 result (collision) explicitly, never
        pick the first silently. The limit exists to bound a pathological
        prefix, not to cap collision detection: `media_send` counts ALL
        rows sharing the handle, so a window cut short would turn a real
        collision into a silent default."""
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
        # The COLUMN, and nothing else. A read here must round-trip what a
        # write put in — a fallback onto `provenance["title"]` would make
        # `get(save(obj)) != obj` for every generated object, which the
        # contract suite is what caught. Generated rows written before
        # migration 0052 carry their title only in provenance; `media_search`
        # is the one reader that cares and it already falls back to the
        # description, and `generate_image` fills the column now.
        title=row.title,
        content=row.content,
    )
