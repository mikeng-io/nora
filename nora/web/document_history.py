"""A document's revision history in the portal (ADR 0020).

Every revision is a full snapshot in `document_revision`, so the history needs no new
table: three routes read and copy what is already there, for ONE viewer.

- `GET /app/v1/documents/{id}/revisions`: every revision, newest first (number, title,
  who wrote it, when, sha256).
- `GET /app/v1/documents/{id}/revisions/{n}`: one revision's stored spec, read-only.
- `POST /app/v1/documents/{id}/revisions/{n}/restore`: publish revision `n`'s spec as a
  NEW revision. History is never rewritten: the old revisions stay, and the restored one
  is `current + 1`.

THE GATE is the document's own read gate, `DocumentStore.get_visible`, before anything
else is read: whoever may open the document may read its history, and a document they may
not read, a deleted one, an unknown id, and a revision number the document never had are
the one 404. An archived
document's history is readable (it still opens by its link), and its restore is refused
like any revision of an archived document (`not_active`). Both reads carry `roomLabel` by
the detail payload's rule: only for a viewer in that room.

LIMITATION (DEC-268): editing something out of a document does
not remove it from its history. Every earlier revision stays readable to whoever may read
the document; deleting it, or forgetting its creator, hides all of it. No redaction.

RESTORE goes through the store's one revision path, `add_revision` with `base_revision`:
the viewer says which revision they were looking at as current, and a revision that landed
since is a `conflict`, never overwritten (the compare-and-swap Nora's own revisions take).
The old spec is checked again with `check_document` before it is written, so what is
stored is what this build judges valid; its pictures are already content addresses and
are carried over as they were. `written_by` is `portal:<person_id>`.

WHO WROTE A REVISION. A revision Nora wrote records the tag it was written at
(`tag:<id>`), not a person, so it is shown as Nora's. A restore records the person
(`portal:<id>`), resolved to a display name by the same resolver the list uses.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from aiohttp import web

from nora.documents.check import check_document
from nora.domain.documents import DocumentStore, RevisionIn, RoomKey, StoredDocument
from nora.persons import Persons
from nora.web.documents import RoomLabel, _room_label

log = logging.getLogger(__name__)

PORTAL_WRITER = "portal:"
_MAX_REVISION = 2**31 - 1


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def writer_of(persons: Persons, written_by: str) -> dict[str, str]:
    """Who wrote a revision, for the portal. A person only when one is recorded."""
    if written_by.startswith(PORTAL_WRITER):
        person_id = written_by[len(PORTAL_WRITER):]
        ref = persons.by_person.get(person_id)
        return {
            "kind": "person",
            "personId": person_id,
            "name": ref.name if ref is not None else person_id,
        }
    return {"kind": "nora"}


async def _readable(
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    person_id: str,
    document_id: UUID,
) -> StoredDocument | None:
    # The document's own read gate: not readable, deleted and unknown are one `None`.
    stored = await store.get_visible(
        document_id=document_id, person_id=person_id, rooms=rooms_of(person_id)
    )
    if stored is None:
        return None
    return stored


async def build_revisions_payload(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    document_id: UUID,
    room_label: RoomLabel | None = None,
) -> dict[str, object] | None:
    current = await _readable(store, rooms_of, person_id, document_id)
    if current is None:
        return None
    history = await store.history(document_id=document_id)
    return {
        "id": str(document_id),
        "title": current.summary.title,
        "revision": current.summary.revision,
        "state": current.summary.state,
        # The detail payload's rule: the room's label only for a viewer in that room.
        "roomLabel": _room_label(rooms_of(person_id), room_label, current.summary),
        "revisions": [
            {
                "revision": h.revision,
                "title": h.title,
                "sha256": h.sha256,
                "recordedAt": _iso(h.recorded_at),
                "writtenBy": writer_of(persons, h.written_by),
            }
            for h in sorted(history, key=lambda h: h.revision, reverse=True)
        ],
    }


async def build_revision_payload(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    persons: Persons,
    person_id: str,
    document_id: UUID,
    revision: int,
    room_label: RoomLabel | None = None,
) -> dict[str, object] | None:
    current = await _readable(store, rooms_of, person_id, document_id)
    if current is None:
        return None
    found = await store.revision(document_id=document_id, revision=revision)
    if found is None:
        return None
    return {
        "id": str(document_id),
        "revision": found.revision,
        "currentRevision": current.summary.revision,
        "state": current.summary.state,
        "roomLabel": _room_label(rooms_of(person_id), room_label, current.summary),
        "title": found.title,
        "sha256": found.sha256,
        "recordedAt": _iso(found.recorded_at),
        "writtenBy": writer_of(persons, found.written_by),
        "document": found.spec,
        "media": list(found.media),
    }


@dataclass(frozen=True)
class RestoreResult:
    """`ok` carries the new revision's number. `not_found` is every reason the viewer must
    not tell apart; `conflict` is a stale `base_revision`; `not_active` an archived
    document; `current` asks to restore the revision that is already current; `invalid` an
    old spec this build's checker refuses; `unavailable` a checker that failed."""

    status: Literal[
        "ok", "not_found", "conflict", "not_active", "current", "invalid", "unavailable"
    ]
    revision: int = 0


async def restore_revision(
    *,
    store: DocumentStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    person_id: str,
    document_id: UUID,
    revision: int,
    base_revision: int,
) -> RestoreResult:
    current = await _readable(store, rooms_of, person_id, document_id)
    if current is None:
        return RestoreResult("not_found")
    old = await store.revision(document_id=document_id, revision=revision)
    if old is None:
        return RestoreResult("not_found")
    # An archived document is refused before anything else is judged, so asking to restore
    # its current revision says the more specific reason (`not_active`), not `current`.
    # `add_revision` refuses it again under its lock, for a change of state made since.
    if current.summary.state != "active":
        return RestoreResult("not_active")
    if revision == current.summary.revision:
        return RestoreResult("current")
    checked = check_document(old.spec)
    if checked.status == "error":
        return RestoreResult("unavailable")
    if not checked.ok or checked.document is None:
        return RestoreResult("invalid")
    outcome = await store.add_revision(
        document_id=document_id,
        base_revision=base_revision,
        revision=RevisionIn(
            title=checked.title,
            spec=checked.document,
            text=checked.text,
            sha256=checked.sha256,
            media=old.media,
            written_by=f"{PORTAL_WRITER}{person_id}",
        ),
    )
    if outcome.status == "ok" and outcome.document is not None:
        return RestoreResult("ok", outcome.document.summary.revision)
    if outcome.status == "not_active":
        # Changed since the read above. Deleted in between is the viewer's
        # 404 like any unreadable document; archived is `not_active`.
        again = await store.get(document_id=document_id)
        if again is None or again.summary.state == "deleted":
            return RestoreResult("not_found")
        return RestoreResult("not_active")
    if outcome.status == "conflict":
        return RestoreResult("conflict")
    return RestoreResult("not_found")


# ---------------------------------------------------------------------------- routes
def _unavailable() -> web.Response:
    return web.json_response(
        {"status": "unavailable", "note": "documents are not available"}, status=503,
    )


def _not_found() -> web.Response:
    return web.json_response({"status": "not_found"}, status=404)


def _refused(note: str) -> web.Response:
    return web.json_response({"status": "refused", "note": note}, status=400)


def _revision_number(text: str) -> int | None:
    if not text.isdigit() or len(text) > 10:
        return None
    n = int(text)
    return n if 1 <= n <= _MAX_REVISION else None


def register_document_history_routes(app: web.Application) -> None:
    """The three history routes, on the app `register_web_routes` already configured
    (`web_documents`, `web_rooms_of`, `web_persons`)."""

    def _viewer(request: web.Request) -> str | None:
        person_id = request.query.get("personId")
        if not person_id or request.app["web_persons"].by_person.get(person_id) is None:
            return None
        return person_id

    def _document_id(request: web.Request) -> UUID | None:
        try:
            return UUID(request.match_info["id"])
        except ValueError:
            return None

    async def revisions(request: web.Request) -> web.Response:
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _unavailable()
        person_id = _viewer(request)
        if person_id is None:
            return _refused("a valid personId is required")
        document_id = _document_id(request)
        if document_id is None:
            return _refused("bad id")
        try:
            body = await build_revisions_payload(
                store=a["web_documents"], rooms_of=a["web_rooms_of"],
                persons=a["web_persons"], person_id=person_id, document_id=document_id,
                room_label=a["web_room_label"],
            )
        except Exception:  # a store that is down is "could not check", never "not found"
            log.exception("document history read failed")
            return _unavailable()
        return _not_found() if body is None else web.json_response(body)

    async def one_revision(request: web.Request) -> web.Response:
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _unavailable()
        person_id = _viewer(request)
        if person_id is None:
            return _refused("a valid personId is required")
        document_id = _document_id(request)
        number = _revision_number(request.match_info["n"])
        if document_id is None or number is None:
            return _refused("bad id")
        try:
            body = await build_revision_payload(
                store=a["web_documents"], rooms_of=a["web_rooms_of"],
                persons=a["web_persons"], person_id=person_id, document_id=document_id,
                revision=number, room_label=a["web_room_label"],
            )
        except Exception:
            log.exception("document revision read failed")
            return _unavailable()
        return _not_found() if body is None else web.json_response(body)

    async def restore(request: web.Request) -> web.Response:
        a = request.app
        if a["web_documents"] is None or a["web_rooms_of"] is None:
            return _unavailable()
        person_id = _viewer(request)
        if person_id is None:
            return _refused("a valid personId is required")
        document_id = _document_id(request)
        number = _revision_number(request.match_info["n"])
        if document_id is None or number is None:
            return _refused("bad id")
        try:
            payload = await request.json()
        except ValueError:
            return _refused("bad body")
        base = payload.get("baseRevision") if isinstance(payload, dict) else None
        if not isinstance(base, int) or isinstance(base, bool) or not 1 <= base <= _MAX_REVISION:
            return _refused("baseRevision is required: the current revision you saw")
        try:
            result = await restore_revision(
                store=a["web_documents"], rooms_of=a["web_rooms_of"], person_id=person_id,
                document_id=document_id, revision=number, base_revision=base,
            )
        except Exception:
            log.exception("document restore failed")
            return _unavailable()
        if result.status == "ok":
            return web.json_response({"status": "ok", "revision": result.revision})
        if result.status == "not_found":
            return _not_found()
        if result.status == "unavailable":
            return _unavailable()
        if result.status == "current":
            return _refused("that is already the current revision")
        # conflict, not_active, invalid: the document is fine, this restore is not.
        return web.json_response({"status": result.status}, status=409)

    app.router.add_get("/app/v1/documents/{id}/revisions", revisions)
    app.router.add_get("/app/v1/documents/{id}/revisions/{n}", one_revision)
    app.router.add_post("/app/v1/documents/{id}/revisions/{n}/restore", restore)
