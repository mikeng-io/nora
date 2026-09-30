"""The portal's read side of documents (ADR 0020, design/artifact §6).

Two payloads, both for ONE viewer: what `person_id` may read, which is what they
created plus what was made in a room that names them. The rooms come from a callable
evaluated at the moment of the read, so a removal from a room revokes access; this
module never sees a `Policy` (`nora.web` must not learn about policy loading).

A document the viewer may not read is `None`, the same answer as "no such document":
an id never confirms that a document exists.

The spec is returned as stored, never re-judged here: it passed `check_document`
when it was written, and the portal validates it again before it draws a line
(`parseDocument` in `app/src/lib/documents/catalog.ts`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from nora.domain.documents import DocumentStore, DocumentSummary, RoomKey
from nora.persons import Persons

MAX_PAGE_SIZE = 50


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _creator(persons: Persons, person_id: str) -> dict[str, str]:
    ref = persons.by_person.get(person_id)
    return {"personId": person_id, "name": ref.name if ref is not None else person_id}


def _summary(persons: Persons, s: DocumentSummary) -> dict[str, object]:
    # The room's id is not shown: a viewer needs to know a document is from a room they
    # are in, not which chat id it is. The platform is enough to label it.
    return {
        "id": str(s.id),
        "title": s.title,
        "revision": s.revision,
        "platform": s.platform,
        "createdAt": _iso(s.created_at),
        "updatedAt": _iso(s.updated_at),
        "createdBy": _creator(persons, s.created_by),
    }


async def build_documents_payload(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    page = max(1, page)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    result = await store.list_visible(
        person_id=person_id,
        rooms=rooms_of(person_id),
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return {
        "documents": [_summary(persons, s) for s in result.items],
        "page": page,
        "pageSize": page_size,
        "total": result.total,
    }


async def build_document_payload(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    document_id: UUID,
) -> dict[str, object] | None:
    stored = await store.get_visible(
        document_id=document_id,
        person_id=person_id,
        rooms=rooms_of(person_id),
    )
    if stored is None:
        return None
    return {
        **_summary(persons, stored.summary),
        "document": stored.spec,
        "media": list(stored.media),
    }
