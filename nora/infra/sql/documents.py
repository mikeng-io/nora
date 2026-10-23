"""`DocumentStore` over SQLAlchemy Core (ADR 0020, `nora.domain.documents`).

Two append-only tables: `documents` (identity) and `document_revision` (a snapshot
per revision). The current document is the highest revision of each id; nothing is
updated. A revision written from a stale base raises a unique violation on
`(document_id, revision)`, which `add_revision` reads as `conflict`.

Visibility is part of the read, in SQL: a document is visible to a person who
created it or whose room set contains its `(platform, conversation_id)`.

State is part of the read too. A third append-only table, `document_state`, holds one
row per change; the current state is the latest row (`_state`, a correlated read on
`idx_document_state_latest`), and no row is `active`. Every person-facing read filters
on it in SQL, so a deleted document is never a row a caller could forget to drop.

`add_revision` and `set_state` both lock the document's identity row (`FOR UPDATE`) first,
so a revision and a change of state on one document are serialized: a revision can never
land on a document that was archived or deleted after it read the state.
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
    DocumentState,
    DocumentSummary,
    ListedState,
    RevisionIn,
    RevisionOutcome,
    RevisionRecord,
    RoomKey,
    StoredDocument,
    StoredRevision,
    VisiblePage,
    ensure_utc_document,
    is_handle,
    normalize_query,
)
from nora.infra.sql.schema import DOCUMENT_REVISION, DOCUMENT_STATE, DOCUMENTS

_D = DOCUMENTS.c
_MAX_LIMIT = 1_000
_MAX_OFFSET = 2**31 - 1
_R = DOCUMENT_REVISION.c
_S = DOCUMENT_STATE.c


def _latest_state(document_id: Any) -> sa.Select[Any]:
    return (
        sa.select(_S.state)
        .where(_S.document_id == document_id)
        .order_by(_S.id.desc())
        .limit(1)
    )


def _state_by() -> sa.ColumnElement[str]:
    """Who wrote each document's latest state row, "" when there is none."""
    latest = (
        sa.select(_S.set_by)
        .where(_S.document_id == _D.id)
        .order_by(_S.id.desc())
        .limit(1)
        .correlate(DOCUMENTS)
        .scalar_subquery()
    )
    return sa.func.coalesce(latest, sa.literal(""))


def _lock(document_id: UUID) -> sa.Select[Any]:
    """The identity row, locked: what serializes a revision against a change of state."""
    return sa.select(_D.id).where(_D.id == document_id).with_for_update()


def _state() -> sa.ColumnElement[str]:
    """Each document's current state: its latest `document_state` row, else `active`."""
    latest = _latest_state(_D.id).correlate(DOCUMENTS).scalar_subquery()
    return sa.func.coalesce(latest, sa.literal("active"))


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
        state=r.state,
        state_by=r.state_by,
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


def _current_join() -> sa.Join:
    """`documents` joined to its highest revision."""
    top = (
        sa.select(_R.document_id, sa.func.max(_R.revision).label("top"))
        .group_by(_R.document_id)
        .subquery()
    )
    return DOCUMENTS.join(top, top.c.document_id == _D.id).join(
        DOCUMENT_REVISION,
        sa.and_(_R.document_id == top.c.document_id, _R.revision == top.c.top),
    )


def _current() -> sa.Select[Any]:
    """Each document at its highest revision."""
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
        _state().label("state"),
        _state_by().label("state_by"),
    ).select_from(_current_join())


def _matches(needle: str) -> sa.ColumnElement[bool]:
    """The CURRENT revision's title or search text contains `needle`, ignoring case. A
    plain `ILIKE '%needle%'` with `%`, `_` and the escape character escaped, so a query is
    text and never a pattern. It runs only over what `_visible` and `_state` already kept
    (one person's documents), so it needs no trigram index."""
    return sa.or_(
        _spaced(_R.title).icontains(needle, autoescape=True),
        _spaced(_R.text).icontains(needle, autoescape=True),
    )


def _spaced(column: sa.ColumnElement[str]) -> sa.ColumnElement[str]:
    """`column` with every run of whitespace collapsed to one space: the query is collapsed
    the same way (`normalize_query`), so "two words" finds text that has them on two lines
    (the checker joins a document's parts with a newline) or with a doubled space."""
    return sa.func.regexp_replace(column, r"\s+", " ", "g")


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
                if (await conn.execute(_lock(document_id))).first() is None:
                    return RevisionOutcome("not_found")
                head = await conn.execute(
                    sa.select(sa.func.max(_R.revision)).where(_R.document_id == document_id)
                )
                top = head.scalar_one()
                if top is None:
                    return RevisionOutcome("not_found")
                state = (
                    await conn.execute(_latest_state(document_id))
                ).scalar_one_or_none()
                if state not in (None, "active"):
                    # Archived or deleted: restore it first. Checked before the base, so a
                    # stale base on an archived document still says why it cannot move.
                    return RevisionOutcome("not_active")
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

    async def set_state(
        self,
        *,
        document_id: UUID,
        state: DocumentState,
        set_by: str,
    ) -> bool:
        async with self._engine.begin() as conn:
            if (await conn.execute(_lock(document_id))).first() is None:
                return False
            await conn.execute(
                sa.insert(DOCUMENT_STATE).values(
                    document_id=document_id, state=state, set_by=set_by
                )
            )
        return True

    async def get(self, *, document_id: UUID) -> StoredDocument | None:
        async with self._engine.connect() as conn:
            return await self._one(conn, document_id)

    async def history(self, *, document_id: UUID) -> tuple[RevisionRecord, ...]:
        stmt = (
            sa.select(_R.revision, _R.title, _R.sha256, _R.written_by, _R.recorded_at, _R.media)
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
                media=tuple(r.media),
            )
            for r in rows
        )

    async def revision(self, *, document_id: UUID, revision: int) -> StoredRevision | None:
        stmt = sa.select(
            _R.revision, _R.title, _R.spec, _R.text, _R.sha256, _R.media, _R.written_by,
            _R.recorded_at,
        ).where(_R.document_id == document_id, _R.revision == revision)
        async with self._engine.connect() as conn:
            r = (await conn.execute(stmt)).first()
        if r is None:
            return None
        return StoredRevision(
            revision=r.revision,
            title=r.title,
            spec=r.spec,
            text=r.text,
            sha256=r.sha256,
            media=tuple(r.media),
            written_by=r.written_by,
            recorded_at=ensure_utc_document(r.recorded_at, field="recorded_at"),
        )

    async def list_visible(
        self,
        *,
        person_id: str,
        rooms: frozenset[RoomKey],
        limit: int,
        offset: int,
        state: ListedState = "active",
        query: str = "",
    ) -> VisiblePage:
        # `deleted` is never a listing, whatever a caller passes.
        listed = "archived" if state == "archived" else "active"
        where = sa.and_(_visible(person_id, rooms), _state() == listed)
        needle = normalize_query(query)
        if needle:
            where = sa.and_(where, _matches(needle))
        # Bounded here as well as by the caller: an unbounded OFFSET is a DataError, and
        # the fake answers an empty page, so the two stores must agree at the extremes.
        limit = min(max(limit, 0), _MAX_LIMIT)
        offset = min(max(offset, 0), _MAX_OFFSET)
        # A search filters on the current revision's columns, so its count reads the same
        # join the page does; an unfiltered list counts the identity rows alone.
        count = sa.select(sa.func.count()).select_from(
            _current_join() if needle else DOCUMENTS
        ).where(where)
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

    async def list_room(
        self,
        *,
        platform: str,
        conversation_ids: frozenset[str],
        limit: int,
        person_id: str,
        rooms: frozenset[RoomKey],
        states: tuple[DocumentState, ...] = ("active",),
    ) -> tuple[DocumentSummary, ...]:
        limit = min(max(limit, 0), _MAX_LIMIT)
        if limit == 0 or not conversation_ids:
            return ()
        ids = sorted(conversation_ids)
        # The visibility is part of the room filter, so the limit below counts only rows the
        # asker may read (the same predicate `list_visible` applies).
        in_room = sa.and_(
            _D.platform == platform,
            _D.conversation_id.in_(ids),
            _state().in_(states),
            _visible(person_id, rooms),
        )
        # The top-revision subquery is restricted to THIS room's documents first, so a turn's
        # read does not aggregate every revision of every document.
        top = (
            sa.select(_R.document_id, sa.func.max(_R.revision).label("top"))
            .where(_R.document_id.in_(sa.select(_D.id).where(in_room)))
            .group_by(_R.document_id)
            .subquery()
        )
        stmt = (
            sa.select(
                _D.id,
                _D.platform,
                _D.conversation_id,
                _D.created_by,
                _D.recorded_at.label("created_at"),
                _R.revision,
                _R.title,
                _R.recorded_at,
                _state().label("state"),
                _state_by().label("state_by"),
            )
            .select_from(
                DOCUMENTS.join(top, top.c.document_id == _D.id).join(
                    DOCUMENT_REVISION,
                    sa.and_(_R.document_id == top.c.document_id, _R.revision == top.c.top),
                )
            )
            .where(in_room)
            .order_by(_R.recorded_at.desc(), _D.id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_summary(r, r.created_at) for r in rows)

    async def get_visible(
        self, *, document_id: UUID, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        stmt = _current().where(
            _D.id == document_id, _visible(person_id, rooms), _state() != "deleted"
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return None if row is None else _stored(row)

    async def get_visible_by_handle(
        self, *, handle: str, person_id: str, rooms: frozenset[RoomKey]
    ) -> StoredDocument | None:
        if not is_handle(handle):  # nothing else ever reaches the query
            return None
        stmt = (
            _current()
            .where(
                _visible(person_id, rooms),
                _state() != "deleted",
                sa.func.replace(sa.cast(_D.id, sa.Text), "-", "").startswith(
                    handle, autoescape=True
                ),
            )
            .limit(2)  # two is already "ambiguous"; never read more
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return _stored(rows[0]) if len(rows) == 1 else None

    async def find_by_handle(self, *, handle: str, limit: int = 10) -> tuple[StoredDocument, ...]:
        if not is_handle(handle):  # nothing else ever reaches the query
            return ()
        stmt = (
            _current()
            .where(
                sa.func.replace(sa.cast(_D.id, sa.Text), "-", "").startswith(
                    handle, autoescape=True
                )
            )
            .order_by(_D.id)
            .limit(min(max(limit, 0), _MAX_LIMIT))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_stored(r) for r in rows)

    async def _one(self, conn: AsyncConnection, document_id: UUID) -> StoredDocument | None:
        row = (await conn.execute(_current().where(_D.id == document_id))).first()
        return None if row is None else _stored(row)

    async def aclose(self) -> None:
        await self._engine.dispose()
