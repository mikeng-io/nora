"""The portal's read side of documents (ADR 0020).

Two payloads, both for ONE viewer: what `person_id` may read, which is what they
created plus what was made in a room that names them. The rooms come from a callable
evaluated at the moment of the read, so a removal from a room revokes access; this
module never sees a `Policy` (`nora.web` must not learn about policy loading).

A document the viewer may not read is `None`, the same answer as "no such document":
an id never confirms that a document exists.

A SEARCH is the same list with `query`: the store keeps only the documents whose current
title or text contains it, in the very statement that applies the viewer's visibility and the
state filter, so a search never reaches a document the list would not show (a deleted one, an
archived one outside the Archived filter, one the viewer may not read).

A document's STATE (archived, deleted) is filtered by the store in SQL: the default list
is the active documents, `state=archived` lists the archived ones, and a deleted document
is the same `None` as one that never existed, on the detail, the handle and the picture
route alike. `change_document_state` is the portal's one write: the viewer who may READ a
document may archive, delete or restore it, through the same `get_visible` gate, and a
deleted one is out of reach here (only Nora's `document_set_status`, in its room, or the
database restores it).

The spec is returned as stored, never re-judged here: it passed `check_document`
when it was written, and the portal validates it again before it draws a line
(`parseDocument` in `app/src/lib/documents/catalog.ts`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from nora.domain.documents import (
    DOCUMENT_STATES,
    DocumentState,
    DocumentStore,
    DocumentSummary,
    ListedState,
    RoomKey,
    StoredDocument,
    is_handle,
    normalize_query,
)
from nora.domain.ports import BlobStore
from nora.persons import Persons

#: (platform, conversation_id) -> the operator's label of that room, "" when none.
RoomLabel = Callable[[str, str], str]

MAX_PAGE_SIZE = 50
# A page number past this is a mistake or an attack, never a place to be: the SQL store's
# OFFSET is a 32-bit-safe integer (a 30-digit `page` would be a 500).
MAX_PAGE = 100_000


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _creator(persons: Persons, person_id: str) -> dict[str, str]:
    ref = persons.by_person.get(person_id)
    return {"personId": person_id, "name": ref.name if ref is not None else person_id}


def _room_label(
    rooms: frozenset[RoomKey], label_of: RoomLabel | None, s: DocumentSummary
) -> str:
    """The room's label, only for a viewer whose own rooms include it. A creator who has
    since left the room still reads their document, but is not told what the room is
    called: the label of a room a person is not in is not theirs to see."""
    if label_of is None or (s.platform, s.conversation_id) not in rooms:
        return ""
    return label_of(s.platform, s.conversation_id)


def _summary(persons: Persons, s: DocumentSummary, room_label: str = "") -> dict[str, object]:
    # The room's id is not shown: a viewer needs to know a document is from a room they
    # are in, not which chat id it is. The platform and the room's label are enough.
    return {
        "id": str(s.id),
        "title": s.title,
        "revision": s.revision,
        "platform": s.platform,
        "roomLabel": room_label,
        "createdAt": _iso(s.created_at),
        "updatedAt": _iso(s.updated_at),
        "createdBy": _creator(persons, s.created_by),
        "state": s.state,
    }


async def build_documents_payload(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    page: int,
    page_size: int,
    state: ListedState = "active",
    query: str = "",
    room_label: RoomLabel | None = None,
) -> dict[str, object]:
    page = min(max(1, page), MAX_PAGE)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    rooms = rooms_of(person_id)
    needle = normalize_query(query)
    result = await store.list_visible(
        person_id=person_id,
        rooms=rooms,
        limit=page_size,
        offset=(page - 1) * page_size,
        state=state,
        query=needle,
    )
    return {
        "documents": [
            _summary(persons, s, _room_label(rooms, room_label, s)) for s in result.items
        ],
        "page": page,
        "pageSize": page_size,
        "total": result.total,
        "state": state,
        "query": needle,
    }


@dataclass(frozen=True)
class StateChange:
    """The portal's state write. `ok` carries the document's new summary; `not_found` is
    every reason the viewer must not tell apart (no such document, not theirs, deleted);
    `bad_state` is a state that is not one of `DOCUMENT_STATES`."""

    status: Literal["ok", "not_found", "bad_state"]
    body: dict[str, object] | None = None


async def change_document_state(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    document_id: UUID,
    state: str,
    room_label: RoomLabel | None = None,
) -> StateChange:
    if state not in DOCUMENT_STATES:
        return StateChange("bad_state")
    # The read gate IS the write gate: whoever may open it (its creator, or a person a
    # room it was made in names) may archive, delete or restore it. A deleted document
    # is not visible, so it cannot be restored from here.
    rooms = rooms_of(person_id)
    stored = await store.get_visible(document_id=document_id, person_id=person_id, rooms=rooms)
    if stored is None:
        return StateChange("not_found")
    wanted = cast(DocumentState, state)
    if stored.summary.state != wanted and not await store.set_state(
        document_id=document_id, state=wanted, set_by=person_id
    ):
        return StateChange("not_found")
    summary = replace(stored.summary, state=wanted)
    return StateChange("ok", _summary(persons, summary, _room_label(rooms, room_label, summary)))


async def build_document_payload(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    document_id: UUID,
    room_label: RoomLabel | None = None,
) -> dict[str, object] | None:
    rooms = rooms_of(person_id)
    stored = await store.get_visible(document_id=document_id, person_id=person_id, rooms=rooms)
    return _detail(persons, stored, _label_of(rooms, room_label, stored))


async def build_document_payload_by_handle(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    handle: str,
    room_label: RoomLabel | None = None,
) -> dict[str, object] | None:
    """`build_document_payload` for a link's 12-hex handle. The prefix is matched among the
    documents `person_id` may read ONLY, and a handle that matches none, matches more than
    one, or is not a handle is the same `None` as an unreadable document: the answer never
    tells a miss from a document the viewer may not see, or from a collision."""
    if not is_handle(handle):
        return None
    rooms = rooms_of(person_id)
    stored = await store.get_visible_by_handle(handle=handle, person_id=person_id, rooms=rooms)
    return _detail(persons, stored, _label_of(rooms, room_label, stored))


def _label_of(
    rooms: frozenset[RoomKey], label_of: RoomLabel | None, stored: StoredDocument | None
) -> str:
    return "" if stored is None else _room_label(rooms, label_of, stored.summary)


def _detail(
    persons: Persons, stored: StoredDocument | None, room_label: str = ""
) -> dict[str, object] | None:
    if stored is None:
        return None
    return {
        **_summary(persons, stored.summary, room_label),
        "document": stored.spec,
        "media": list(stored.media),
    }


# ----------------------------------------------------------------------- picture bytes
_FULL_SHA = re.compile(r"[0-9a-f]{64}")


def sniff_raster(data: bytes) -> str | None:
    """The media type of `data` when its own first bytes say it is a PNG, JPEG, GIF or
    WebP, else `None`.

    The type is read off the bytes, never taken from the media index or the sender's
    label: a document may only ever make the portal serve a raster picture, and an SVG
    (which can carry script), HTML or anything else is `None` however it was labelled.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True)
class DocumentMedia:
    """One picture of one document, or why there is none. `not_found` is one answer
    for every reason the viewer must not tell apart: no such document, not theirs, a
    picture the document does not name, bytes that are gone, bytes that are not a raster
    picture. `unavailable` is only "could not check"."""

    status: Literal["ok", "not_found", "unavailable"]
    media_type: str = ""
    data: bytes = b""


def _not_found() -> DocumentMedia:
    return DocumentMedia("not_found")


async def build_document_media(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    blobs: BlobStore | None,
    person_id: str,
    document_id: UUID,
    sha: str,
) -> DocumentMedia:
    # The address is a full sha256 in lower case: it is compared with what the document
    # names, so a prefix or a different case must not match by accident.
    if not _FULL_SHA.fullmatch(sha):
        return _not_found()
    try:
        stored = await store.get_visible(
            document_id=document_id, person_id=person_id, rooms=rooms_of(person_id),
        )
    except Exception:  # noqa: BLE001 - a store that is down is "could not check"
        return DocumentMedia("unavailable")
    # Readable, AND named by one of ITS revisions (the current one, or an earlier one the
    # history view shows): being able to open a document is not a
    # licence to read any picture Nora holds.
    if stored is None:
        return _not_found()
    if sha not in stored.media:
        try:
            history = await store.history(document_id=document_id)
        except Exception:  # noqa: BLE001
            return DocumentMedia("unavailable")
        if not any(sha in h.media for h in history):
            return _not_found()
    if blobs is None:
        return DocumentMedia("unavailable")
    try:
        data = await blobs.get(key=sha)
    except Exception:  # noqa: BLE001
        return DocumentMedia("unavailable")
    if not data:
        return _not_found()
    media_type = sniff_raster(data)
    if media_type is None:
        return _not_found()
    return DocumentMedia("ok", media_type, data)
