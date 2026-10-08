"""The portal's Artifacts section (ADR 0023): the files Nora sent out of her computer in
the rooms that name the viewer, and their bytes.

WHAT IS LISTED. Every `chat` export and, per link, the newest `link` export, of the
rooms `rooms_of(person_id)` names at the moment of the read: leaving a room revokes
every file of it on the next request. A `keep` export is Nora's private record and is
never listed, opened or downloaded. The store applies the rule in the statement that
reads the row (`ComputerExportStore.list_shared`, `shared_link`, `shared_attachment`).

HOW ONE IS ADDRESSED. A link by its `artifact_id` (12 hex, what its URL carries), so a
revision keeps its address and the portal shows the newest file behind it. An
attachment has no `artifact_id`, so by its row `id` (a UUID): unique, unguessable, and
one address per file sent. The sha256 is not an address: the same bytes sent in two
rooms are two files with two rooms, and a content address would make one of them
answer for the other.

NOT VISIBLE IS NOT FOUND. A file the viewer may not see, one that does not exist, a
`keep` row and a ref that is neither shape are the one `None` / 404: a ref never
confirms that a file exists.

THE DOWNLOAD IS STRICT (decision 3), because it is served from the origin that holds
the session cookie. The type is read off the BYTES again (`sniff_export_type`), never
taken from the row; a web page is never served. Only a raster picture, audio or video
may display inline; everything else, SVG and PDF included, is an attachment with a
plain ASCII file name. Every response carries `nosniff`, `Content-Security-Policy:
sandbox` and `no-store` (`download_headers`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from aiohttp import web

from nora.domain.computer_exports import (
    ComputerExport,
    ComputerExportStore,
    is_artifact_id,
    sniff_export_type,
)
from nora.domain.documents import DOCUMENT_QUERY_MAX, RoomKey, normalize_query
from nora.domain.ports import BlobStore
from nora.web.documents import MAX_PAGE, MAX_PAGE_SIZE, RoomLabel

log = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 20

#: The types a browser may display in place. Everything else is a download.
INLINE_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)
_INLINE_PREFIXES = ("audio/", "video/")

#: What a download file name may hold: anything else becomes `_`.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_NAME_CHARS = 120
_FULL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_inline(media_type: str) -> bool:
    """Whether a file of `media_type` may display in the browser rather than download."""
    return media_type in INLINE_TYPES or media_type.startswith(_INLINE_PREFIXES)


def download_name(export: ComputerExport) -> str:
    """A plain ASCII file name for `export`: its title with anything but letters, digits,
    `.`, `_` and `-` replaced, plus the workspace file's suffix when the title lacks it.
    Never empty, never a path, never a quote."""
    suffix = ""
    base = export.path.rsplit("/", 1)[-1]
    if "." in base.lstrip("."):
        suffix = "." + base.rsplit(".", 1)[-1]
    name = _UNSAFE_NAME.sub("_", export.title).strip("._-")[:_NAME_CHARS] or "file"
    safe_suffix = _UNSAFE_NAME.sub("_", suffix)
    if safe_suffix and not name.lower().endswith(safe_suffix.lower()):
        name += safe_suffix
    return name


def download_headers(media_type: str, filename: str) -> dict[str, str]:
    """The headers of every file the portal serves. `nosniff` keeps the browser to the
    type given, `sandbox` keeps anything that does render from running as this origin,
    `no-store` keeps a file from outliving a sign-out or a removal from its room."""
    disposition = "inline" if is_inline(media_type) else "attachment"
    return {
        "Content-Type": media_type,
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cache-Control": "private, no-store",
    }


def ref_of(export: ComputerExport) -> str:
    """How the portal addresses `export`: a link's `artifact_id`, an attachment's id."""
    return export.artifact_id if export.destination == "link" and export.artifact_id else str(
        export.id
    )


def _room_label(rooms: frozenset[RoomKey], label_of: RoomLabel | None, e: ComputerExport) -> str:
    if label_of is None or (e.platform, e.conversation_id) not in rooms:
        return ""
    return label_of(e.platform, e.conversation_id)


def _summary(e: ComputerExport, room_label: str) -> dict[str, object]:
    # Neither the room's chat id nor the workspace path is shown: the platform and the
    # room's label say where it was sent; the title names it.
    return {
        "id": ref_of(e),
        "kind": e.destination,
        "title": e.title,
        "mediaType": e.media_type,
        "size": e.size_bytes,
        "platform": e.platform,
        "roomLabel": room_label,
        "createdAt": _iso(e.created_at),
        "revised": e.revised,
    }


async def _find(
    store: ComputerExportStore, rooms: frozenset[RoomKey], ref: str,
) -> ComputerExport | None:
    """The file `ref` names among `rooms`, or None for every reason it is not there."""
    if is_artifact_id(ref):
        return await store.shared_link(artifact_id=ref, rooms=rooms)
    if _FULL_UUID.fullmatch(ref):
        return await store.shared_attachment(export_id=UUID(ref), rooms=rooms)
    return None


async def build_artifacts_payload(
    *,
    store: ComputerExportStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    person_id: str,
    page: int,
    page_size: int,
    query: str = "",
    room_label: RoomLabel | None = None,
) -> dict[str, object]:
    page = min(max(1, page), MAX_PAGE)
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    rooms = rooms_of(person_id)
    needle = normalize_query(query)
    result = await store.list_shared(
        rooms=rooms, limit=page_size, offset=(page - 1) * page_size, query=needle,
    )
    return {
        "artifacts": [_summary(e, _room_label(rooms, room_label, e)) for e in result.items],
        "page": page,
        "pageSize": page_size,
        "total": result.total,
        "query": needle,
    }


async def build_artifact_payload(
    *,
    store: ComputerExportStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    person_id: str,
    ref: str,
    room_label: RoomLabel | None = None,
) -> dict[str, object] | None:
    rooms = rooms_of(person_id)
    found = await _find(store, rooms, ref)
    return None if found is None else _summary(found, _room_label(rooms, room_label, found))


@dataclass(frozen=True)
class ArtifactFile:
    """One file's bytes, or why there are none. `not_found` is every reason the viewer
    must not tell apart; `unavailable` is only "could not check"."""

    status: Literal["ok", "not_found", "unavailable"]
    data: bytes = b""
    headers: dict[str, str] | None = None


async def build_artifact_download(
    *,
    store: ComputerExportStore,
    rooms_of: Callable[[str], frozenset[RoomKey]],
    blobs: BlobStore | None,
    person_id: str,
    ref: str,
) -> ArtifactFile:
    try:
        found = await _find(store, rooms_of(person_id), ref)
    except Exception:  # a store that is down is "could not check"
        log.exception("artifact read failed")
        return ArtifactFile("unavailable")
    if found is None or not found.blob_key:
        return ArtifactFile("not_found")
    if blobs is None:
        return ArtifactFile("unavailable")
    try:
        data = await blobs.get(key=found.blob_key)
    except Exception:
        log.exception("artifact bytes read failed")
        return ArtifactFile("unavailable")
    if not data:
        return ArtifactFile("not_found")
    # Typed again, off the bytes being served: the row's label is what they were, and a
    # page is never served whatever any row says.
    media_type = sniff_export_type(data, found.path)
    if media_type is None:
        return ArtifactFile("not_found")
    return ArtifactFile("ok", data, download_headers(media_type, download_name(found)))


# ----------------------------------------------------------------------------- routes


def register_artifact_routes(app: web.Application) -> None:
    """The three Artifacts routes, on the app `register_web_routes` configured
    (`web_artifacts`, `web_rooms_of`, `web_room_label`, `web_blobs`, `web_persons`)."""

    def _unavailable() -> web.Response:
        return web.json_response(
            {"status": "unavailable", "note": "artifacts are not available"}, status=503,
        )

    def _not_found() -> web.Response:
        # Never a distinct "forbidden": a ref must not confirm a file exists.
        return web.json_response({"status": "not_found"}, status=404)

    def _viewer(request: web.Request) -> str | None:
        person_id = request.query.get("personId")
        if not person_id or request.app["web_persons"].by_person.get(person_id) is None:
            return None
        return person_id

    def _refused(note: str) -> web.Response:
        return web.json_response({"status": "refused", "note": note}, status=400)

    async def artifacts_list(request: web.Request) -> web.Response:
        a = request.app
        if a["web_artifacts"] is None or a["web_rooms_of"] is None:
            return _unavailable()
        person_id = _viewer(request)
        if person_id is None:
            return _refused("a valid personId is required")
        try:
            page = int(request.query.get("page", "1"))
            page_size = int(request.query.get("pageSize", str(_DEFAULT_PAGE_SIZE)))
        except ValueError:
            return web.json_response({"error": "invalid page"}, status=400)
        query = request.query.get("q", "")
        if len(query) > DOCUMENT_QUERY_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in query):
            return web.json_response({"error": "invalid q"}, status=400)
        try:
            body = await build_artifacts_payload(
                store=a["web_artifacts"], rooms_of=a["web_rooms_of"], person_id=person_id,
                page=page, page_size=page_size, query=query, room_label=a["web_room_label"],
            )
        except Exception:  # a store that is down is "could not check", never "nothing"
            log.exception("artifacts list failed")
            return _unavailable()
        return web.json_response(body)

    async def artifacts_detail(request: web.Request) -> web.Response:
        a = request.app
        if a["web_artifacts"] is None or a["web_rooms_of"] is None:
            return _unavailable()
        person_id = _viewer(request)
        if person_id is None:
            return _refused("a valid personId is required")
        try:
            body = await build_artifact_payload(
                store=a["web_artifacts"], rooms_of=a["web_rooms_of"], person_id=person_id,
                ref=request.match_info["ref"], room_label=a["web_room_label"],
            )
        except Exception:
            log.exception("artifact read failed")
            return _unavailable()
        return _not_found() if body is None else web.json_response(body)

    async def artifacts_download(request: web.Request) -> web.Response:
        a = request.app
        if a["web_artifacts"] is None or a["web_rooms_of"] is None:
            return _unavailable()
        person_id = _viewer(request)
        if person_id is None:
            return _refused("a valid personId is required")
        found = await build_artifact_download(
            store=a["web_artifacts"], rooms_of=a["web_rooms_of"], blobs=a["web_blobs"],
            person_id=person_id, ref=request.match_info["ref"],
        )
        if found.status == "unavailable":
            return _unavailable()
        if found.status == "not_found" or found.headers is None:
            return _not_found()
        return web.Response(body=found.data, headers=found.headers)

    app.router.add_get("/app/v1/artifacts", artifacts_list)
    app.router.add_get("/app/v1/artifacts/{ref}", artifacts_detail)
    app.router.add_get("/app/v1/artifacts/{ref}/download", artifacts_download)
