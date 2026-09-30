"""`DocumentStore` over SQLAlchemy Core (ADR 0020, `nora.domain.documents`).

Two append-only tables: `documents` (identity) and `document_revision` (a snapshot
per revision). The current document is the highest revision of each id; nothing is
updated. A revision written from a stale base raises a unique violation on
`(document_id, revision)`, which `add_revision` reads as `conflict`.

Visibility is part of the read, in SQL: a document is visible to a person who
created it or whose room set contains its `(platform, conversation_id)`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.documents import (
    DocumentExistsError,
    DocumentSummary,
    RevisionIn,
    RevisionOutcome,
    RevisionRecord,
    RoomKey,
    StoredDocument,
    VisiblePage,
    ensure_utc_document,
)
from nora.infra.sql.schema import DOCUMENT_REVISION, DOCUMENTS

_D = DOCUMENTS.c
_MAX_LIMIT = 1_000
_MAX_OFFSET = 2**31 - 1
_R = DOCUMENT_REVISION.c


def _summary(r: Row[Any], created_at: Any) -> DocumentSummary:
    return DocumentSummary(
        id=r.id,
        platform=r.platform,
        conversation_id=r.conversation_id,
        created_by=r.created_by,
        title=r.title,
        revision=r.revision,
        created_at=ensure_utc_document(created_at, field="created_at"),
        updated_at=ensure_utc_document(r.recorded_at, field="updated_at"),
    )


def _stored(r: Row[Any]) -> StoredDocument:
    return StoredDocument(
        summary=_summary(r, r.created_at),
        spec=r.spec,
        text=r.text,
        sha256=r.sha256,
        media=tuple(r.media),
        written_by=r.written_by,
    )


def _current() -> sa.Select[Any]:
    """Each document at its highest revision."""
    top = (
        sa.select(_R.document_id, sa.func.max(_R.revision).label("top"))
        .group_by(_R.document_id)
        .subquery()
    )
    return sa.select(
        _D.id,
        _D.platform,
        _D.conversation_id,
        _D.created_by,
        _D.recorded_at.label("created_at"),
        _R.revision,
        _R.title,
        _R.spec,
        _R.text,
        _R.sha256,
        _R.media,
        _R.written_by,
        _R.recorded_at,
    ).select_from(
        DOCUMENTS.join(top, top.c.document_id == _D.id).join(
            DOCUMENT_REVISION,
            sa.and_(_R.document_id == top.c.document_id, _R.revision == top.c.top),
        )
    )


def _visible(person_id: str, rooms: frozenset[RoomKey]) -> sa.ColumnElement[bool]:
    mine = _D.created_by == person_id
    if not rooms:
        return mine
    return sa.or_(mine, sa.tuple_(_D.platform, _D.conversation_id).in_(sorted(rooms)))


def _revision_values(document_id: UUID, number: int, revision: RevisionIn) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "revision": number,
        "title": revision.title,
        "spec": revision.spec,
        "text": revision.text,
        "sha256": revision.sha256,
        "media": list(revision.media),
        "written_by": revision.written_by,
    }


class SqlDocumentStore:
    """Satisfies `nora.domain.documents.DocumentStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self,
        *,
        document_id: UUID,
        platform: str,
        conversation_id: str,
        created_by: str,
        revision: RevisionIn,
    ) -> StoredDocument:
        async with self._engine.begin() as conn:
            try:
                await conn.execute(
                    sa.insert(DOCUMENTS).values(
                        id=document_id,
                        platform=platform,
                        conversation_id=conversation_id,
                        created_by=created_by,
                    )
                )
            except IntegrityError as exc:
                raise DocumentExistsError(str(document_id)) from exc
            await conn.execute(
                sa.insert(DOCUMENT_REVISION).values(_revision_values(document_id, 1, revision))
            )
            stored = await self._one(conn, document_id)
        if stored is None:  # written in this transaction a moment ago
            raise RuntimeError(f"document {document_id} vanished inside its own transaction")
        return stored

    async def add_revision(
        self, *, document_id: UUID, base_revision: int, revision: RevisionIn
    ) -> RevisionOutcome:
        try:
            async with self._engine.begin() as conn:
                head = await conn.execute(
                    sa.select(sa.func.max(_R.revision)).where(_R.document_id == document_id)
                )
                top = head.scalar_one()
                if top is None:
                    return RevisionOutcome("not_found")
                if top != base_revision:
                    return RevisionOutcome("conflict")
                await conn.execute(
                    sa.insert(DOCUMENT_REVISION).values(
                        _revision_values(document_id, base_revision + 1, revision)
                    )
                )
                stored = await self._one(conn, document_id)
        except IntegrityError:
            # Another writer took `base_revision + 1` between the read and the insert.
            return RevisionOutcome("conflict")
        return RevisionOutcome("ok", stored)

    async def get(self, *, document_id: UUID) -> StoredDocument | None:
        async with self._engine.connect() as conn:
            return await self._one(conn, document_id)

    async def history(self, *, document_id: UUID) -> tuple[RevisionRecord, ...]:
        stmt = (
            sa.select(_R.revision, _R.title, _R.sha256, _R.written_by, _R.recorded_at)
            .where(_R.document_id == document_id)
            .order_by(_R.revision)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(
            RevisionRecord(
                revision=r.revision,
                title=r.title,
                sha256=r.sha256,
                written_by=r.written_by,
                recorded_at=ensure_utc_document(r.recorded_at, field="recorded_at"),
            )
            for r in rows
        )

    async def list_visible(
        self, *, person_id: str, rooms: frozenset[RoomKey], limit: int, offset: int
    ) -> VisiblePage:
        where = _visible(person_id, rooms)
        # Bounded here as well as by the caller: an unbounded OFFSET is a DataError, and
        # the fake answers an empty page, so the two stores must agree at the extremes.
        limit = min(max(limit, 0), _MAX_LIMIT)
        offset = min(max(offset, 0), _MAX_OFFSET)
        count = sa.select(sa.func.count()).select_from(DOCUMENTS).where(where)
        page = (
            _current()
            .where(where)
            .order_by(_R.recorded_at.desc(), _D.id)
            .limit(limit)
            .offset(offset)
        )
        async with self._engine.connect() as conn:
            total = (await conn.execute(count)).scalar_one()
            rows = (await conn.execute(page)).all() if limit > 0 else []
        return VisiblePage(
            items=tuple(_summary(r, r.created_at) for r in rows),
            total=int(total),
        )

    async def get_visible(
        self, *, document_id: UUID, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        stmt = _current().where(_D.id == document_id, _visible(person_id, rooms))
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _stored(row)

    async def _one(self, conn: AsyncConnection, document_id: UUID) -> StoredDocument | None:
        row = (await conn.execute(_current().where(_D.id == document_id))).first()
        return None if row is None else _stored(row)

    async def aclose(self) -> None:
        await self._engine.dispose()
