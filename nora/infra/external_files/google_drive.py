"""`GoogleDriveSource`: the first `ExternalFilesSource`, Google Drive read-only
(design/external-files/00-design.md §4, build step S2).

Nora's own account (`nora@67.zone`), through her ONE Google identity
(`nora.infra.google.auth.GoogleCredentials`, shared by every Google service
she uses). This adapter declares ONE scope, `drive.readonly`
(`nora.infra.google.scopes`): nothing here can write, comment or share, and
the Sheets reads accept that scope. The
vendor's vocabulary (MIME types, `files.export`, the Sheets ranges) lives in
this module and nowhere above it; the port speaks `ExternalFile`/`FilePart`.

**Calls, all at open time, none at ingest.**

| need | call |
|---|---|
| describe | `files.get`, fields `id,name,mimeType,modifiedTime,size,trashed,owners(displayName)` |
| Google Doc | `files.export` as `text/markdown` |
| Google Sheet | `spreadsheets.get` (tab names), then `values:batchGet` for every tab |
| Google Slides | `files.export` as `text/plain` |
| PDF, docx, xlsx, pptx, plain text | `files.get?alt=media`, parsed by `text` |

`trashed` is the one field beyond the design's list: Drive answers a file in
the bin with its metadata, and that is the only "deleted" this account can
tell apart from "not shared" (both of those are a bare 404, below).
`supportsAllDrives=true` rides on every Drive call so a shared-drive file
resolves; the Sheets API has no such parameter. A ref's resource key goes in
`X-Goog-Drive-Resource-Keys` on every call.

**Never cached (operator, 2026-10-10).** Every `describe`, `read` and
`fetch_bytes` fetches afresh: `read` does its own `files.get` for the kind
rather than trusting an earlier `describe`. The access token is the shared
credentials' (in memory until its own expiry, a credential, not anything
fetched through a link); this adapter never holds the client secret or the
refresh token, and never logs a file id (a log line names the six-hex handle).

**How a failure reads** (`ExternalFileError.status`, reason code):

- 404 (Drive does not say whether the file exists): `refused`, `NOT_SHARED`.
  It is the common case, a link nobody shared with her, and the fix is the
  same either way: share it, or send the live link.
- 403 for a permission reason: `refused`, `NOT_SHARED`; `cannotDownloadFile`
  and its kin: `refused`, `NO_DOWNLOAD`; `exportSizeLimitExceeded` (Google's
  10 MB export cap) or more than `max_bytes` downloaded: `refused`,
  `TOO_LARGE`; a rate limit, an API not enabled: `unavailable`.
- `trashed`: `nothing_found`, `DELETED`.
- No access token (`invalid_grant`, `drive.readonly` not granted to the
  shared refresh token, the token endpoint down): `unavailable`, with the
  credentials' reason; the credentials log and count it.
- 429, 5xx, a transport error, a body that is not JSON: `unavailable`.

Never raises anything but `ExternalFileError`.
"""

from __future__ import annotations

import json
import logging
import zlib
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from nora.domain.external_files import (
    DELETED,
    NO_DOWNLOAD,
    NO_PART,
    NOT_SHARED,
    NOT_TEXT,
    TOO_LARGE,
    ExternalFile,
    ExternalFileError,
    ExternalFileRef,
    FileKind,
    FilePart,
    recognise,
)

from ..google.auth import GoogleAuthError, GoogleCredentials
from ..google.scopes import DRIVE_READONLY
from .text import docx_text, pages, pdf_text, plain_text, pptx_text, table_text, xlsx_text

log = logging.getLogger("nora.external_files")

__all__ = ["DRIVE_SCOPE", "MAX_BYTES", "PART_CHARS", "GoogleDriveSource"]


#: The one scope this consumer declares (`nora.infra.google.scopes`).
DRIVE_SCOPE = DRIVE_READONLY
DRIVE_URL = "https://www.googleapis.com/drive/v3/files"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"

#: How many characters one part of a file carries. A tool result is re-sent on
#: every later model call of the turn, so a part is bounded like a fetched
#: page; a longer file is paged (part i of n), never cut silently.
PART_CHARS = 12_000
#: The most one open downloads, export or file. Google refuses an export over
#: about 10 MB itself (`exportSizeLimitExceeded`); the same bound on a
#: download keeps one open from buffering a 2 GB video.
MAX_BYTES = 10 * 1024 * 1024

_FIELDS = "id,name,mimeType,modifiedTime,size,trashed,owners(displayName)"
_GDOC = "application/vnd.google-apps.document"
_GSHEET = "application/vnd.google-apps.spreadsheet"
_GSLIDES = "application/vnd.google-apps.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: A Google-native file: its kind, the export its text is read from (None: the
#: Sheets API), and the Office format a copy to her computer is exported as.
_NATIVE: dict[str, tuple[FileKind, str | None, str]] = {
    _GDOC: ("document", "text/markdown", _DOCX),
    _GSHEET: ("spreadsheet", None, _XLSX),
    _GSLIDES: ("presentation", "text/plain", _PPTX),
}
#: A stored file: its kind and how its downloaded bytes are read as text.
_BLOBS: dict[str, tuple[FileKind, Callable[[bytes, int], str]]] = {
    "application/pdf": ("pdf", lambda data, _cap: pdf_text(data)),
    _DOCX: ("document", docx_text),
    _XLSX: ("spreadsheet", xlsx_text),
    _PPTX: ("presentation", pptx_text),
    "text/plain": ("document", lambda data, _cap: plain_text(data)),
    "text/markdown": ("document", lambda data, _cap: plain_text(data)),
    "text/csv": ("spreadsheet", lambda data, _cap: plain_text(data)),
}

_PERMISSION_REASONS = frozenset({
    "insufficientFilePermissions", "forbidden", "appNotAuthorizedToFile",
    "domainPolicy", "PERMISSION_DENIED", "insufficientPermissions",
})
_NO_DOWNLOAD_REASONS = frozenset({"cannotDownloadFile", "cannotExportFile", "cannotCopyFile"})
_TOO_LARGE_REASONS = frozenset({"exportSizeLimitExceeded"})


def kind_of(mime: str) -> FileKind:
    """The one vocabulary every adapter maps onto (§2)."""
    if mime in _NATIVE:
        return _NATIVE[mime][0]
    if mime in _BLOBS:
        return _BLOBS[mime][0]
    return "other"


def _modified_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _reason(body: bytes) -> str:
    """Drive's `errors[0].reason`, or the Sheets API's `status`, or ''."""
    try:
        error = json.loads(body).get("error", {})
    except (ValueError, AttributeError):
        return ""
    if not isinstance(error, dict):
        return ""
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("reason", ""))
    return str(error.get("status", ""))


def _failure(status: int, body: bytes) -> ExternalFileError:
    reason = _reason(body)
    if status == 404:
        return ExternalFileError("refused", NOT_SHARED)
    if status == 403 and reason in _TOO_LARGE_REASONS:
        return ExternalFileError("refused", TOO_LARGE)
    if status == 403 and reason in _NO_DOWNLOAD_REASONS:
        return ExternalFileError("refused", NO_DOWNLOAD)
    if status == 403 and reason in _PERMISSION_REASONS:
        return ExternalFileError("refused", NOT_SHARED)
    # A rate limit, an API not enabled, a 5xx, anything unforeseen: she could
    # not ask, which is never "not shared".
    return ExternalFileError("unavailable", f"http {status} {reason}".strip())


async def _bounded_body(resp: httpx.Response, max_bytes: int) -> bytes:
    """`resp`'s body, refused (`TOO_LARGE`) the moment it would pass
    `max_bytes`. Read RAW, so the count is of bytes actually received and
    nothing is decoded unbounded first; a body the server compressed anyway
    (gzip or deflate) is inflated here with each step's output capped at what
    is left of the budget. Any other encoding is not read at all."""
    encoding = resp.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("identity", "", "gzip", "deflate"):
        raise ExternalFileError("unavailable", f"unexpected content encoding {encoding[:20]}")
    inflate = zlib.decompressobj(47) if encoding in ("gzip", "deflate") else None
    body = bytearray()
    async for raw in resp.aiter_raw():
        pending = raw
        while pending:
            if inflate is None:
                chunk, pending = pending, b""
            else:
                chunk = inflate.decompress(pending, max_bytes - len(body) + 1)
                pending = inflate.unconsumed_tail
            if len(body) + len(chunk) > max_bytes:
                raise ExternalFileError("refused", TOO_LARGE)
            body += chunk
            if inflate is not None and not chunk:
                break
    return bytes(body)


class GoogleDriveSource:
    """One process-lifetime adapter, built by the composition root under
    `[external_files]` when its three environment variables are set."""

    name = "google_drive"

    def __init__(
        self, *, credentials: GoogleCredentials, part_chars: int = PART_CHARS,
        max_bytes: int = MAX_BYTES, timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Nora's one Google identity (`nora.infra.google.auth`): this adapter
        # asks it for an access token for `DRIVE_SCOPE` and never holds the
        # client secret or the refresh token.
        self._credentials = credentials
        self._part_chars = part_chars
        self._max_bytes = max_bytes
        self._timeout = httpx.Timeout(timeout_seconds)
        # The seam a test drives (`httpx.MockTransport`); None is real HTTPS.
        self._transport = transport

    # ---- the port ---------------------------------------------------------

    def recognise(self, *, url: str) -> ExternalFileRef | None:
        ref = recognise(url)
        return ref if ref is not None and ref.provider == self.name else None

    async def describe(self, *, ref: ExternalFileRef) -> ExternalFile:
        meta = await self._meta(ref)
        owners = meta.get("owners")
        owner = ""
        if isinstance(owners, list) and owners and isinstance(owners[0], dict):
            owner = str(owners[0].get("displayName") or "")
        return ExternalFile(
            ref=ref, name=str(meta.get("name") or ""),
            kind=kind_of(str(meta.get("mimeType") or "")),
            owner=owner, modified_ms=_modified_ms(meta.get("modifiedTime")),
        )

    async def read(self, *, ref: ExternalFileRef, part: int) -> FilePart:
        meta = await self._meta(ref)
        text = await self._text(ref, meta)
        parts = pages(text, self._part_chars)
        if not 1 <= part <= len(parts):
            raise ExternalFileError("nothing_found", NO_PART)
        return FilePart(text=parts[part - 1], part=part, parts=len(parts))

    async def fetch_bytes(self, *, ref: ExternalFileRef) -> bytes:
        meta = await self._meta(ref)
        mime = str(meta.get("mimeType") or "")
        if mime in _NATIVE:
            return await self._export(ref, _NATIVE[mime][2])
        if mime.startswith("application/vnd.google-apps."):
            # A form, a drawing, a site: nothing to download or export.
            raise ExternalFileError("refused", NOT_TEXT)
        self._refuse_large(meta)
        return await self._download(ref)

    # ---- reads --------------------------------------------------------------

    async def _meta(self, ref: ExternalFileRef) -> dict[str, Any]:
        body = await self._get(
            ref, f"{DRIVE_URL}/{quote(ref.file_id, safe='')}",
            {"fields": _FIELDS, "supportsAllDrives": "true"},
        )
        meta = self._json(body)
        if meta.get("trashed") is True:
            raise ExternalFileError("nothing_found", DELETED)
        return meta

    async def _text(self, ref: ExternalFileRef, meta: dict[str, Any]) -> str:
        mime = str(meta.get("mimeType") or "")
        if mime == _GSHEET:
            return await self._sheet(ref)
        if mime in _NATIVE:
            export = _NATIVE[mime][1]
            assert export is not None  # noqa: S101 - only a Sheet has none, handled above
            return plain_text(await self._export(ref, export))
        if mime not in _BLOBS:
            raise ExternalFileError("refused", NOT_TEXT)
        self._refuse_large(meta)
        return _BLOBS[mime][1](await self._download(ref), self._max_bytes)

    def _refuse_large(self, meta: dict[str, Any]) -> None:
        size = meta.get("size")
        if isinstance(size, str) and size.isdigit() and int(size) > self._max_bytes:
            raise ExternalFileError("refused", TOO_LARGE)

    async def _export(self, ref: ExternalFileRef, mime: str) -> bytes:
        return await self._get(
            ref, f"{DRIVE_URL}/{quote(ref.file_id, safe='')}/export",
            {"mimeType": mime, "supportsAllDrives": "true"},
        )

    async def _download(self, ref: ExternalFileRef) -> bytes:
        return await self._get(
            ref, f"{DRIVE_URL}/{quote(ref.file_id, safe='')}",
            {"alt": "media", "supportsAllDrives": "true"},
        )

    async def _sheet(self, ref: ExternalFileRef) -> str:
        """Every tab, in order, each as its name and a table. A CSV export
        would return the first tab only (§4)."""
        base = f"{SHEETS_URL}/{quote(ref.file_id, safe='')}"
        book = self._json(await self._get(
            ref, base, {"fields": "sheets.properties(title,sheetType)"},
        ))
        titles = [
            str(s["properties"].get("title", ""))
            for s in book.get("sheets", [])
            if isinstance(s, dict) and isinstance(s.get("properties"), dict)
            and s["properties"].get("sheetType", "GRID") == "GRID"
        ]
        if not titles:
            return ""
        ranges = [("ranges", "'" + t.replace("'", "''") + "'") for t in titles]
        values = self._json(await self._get(
            ref, f"{base}/values:batchGet",
            httpx.QueryParams(
                [*ranges, ("majorDimension", "ROWS"), ("valueRenderOption", "FORMATTED_VALUE")],
            ),
        ))
        got = values.get("valueRanges", [])
        tabs = []
        for i, title in enumerate(titles):
            vr = got[i] if i < len(got) and isinstance(got[i], dict) else {}
            rows = vr.get("values", [])
            tabs.append(table_text(title, rows if isinstance(rows, list) else []))
        return "\n\n".join(tabs)

    # ---- HTTP ---------------------------------------------------------------

    @staticmethod
    def _json(body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(body)
        except ValueError:
            raise ExternalFileError("unavailable", "the provider did not answer JSON") from None
        if not isinstance(value, dict):
            raise ExternalFileError("unavailable", "the provider did not answer an object")
        return value

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    async def _get(
        self, ref: ExternalFileRef, url: str,
        params: httpx.QueryParams | dict[str, str],
    ) -> bytes:
        """One authorised GET, its body read at most `max_bytes`. A 401 drops
        the access token and tries once more with a fresh one."""
        for attempt in (1, 2):
            # `identity`: the body is counted as it arrives on the wire, so a
            # compressed body cannot expand past the cap inside a decoder.
            headers = {
                "Authorization": f"Bearer {await self._token()}",
                "Accept-Encoding": "identity",
            }
            if ref.resource_key:
                headers["X-Goog-Drive-Resource-Keys"] = f"{ref.file_id}/{ref.resource_key}"
            try:
                async with (
                    self._client() as client,
                    client.stream("GET", url, params=params, headers=headers) as resp,
                ):
                    body = await _bounded_body(resp, self._max_bytes)
                    status = resp.status_code
            except ExternalFileError:
                raise
            except Exception as exc:
                log.warning("external file %s: provider unreachable: %s", ref.handle,
                            type(exc).__name__)
                raise ExternalFileError("unavailable", "the provider could not be reached") from exc
            if status == 200:
                return body
            if status == 401 and attempt == 1:
                self._credentials.invalidate()
                continue
            failure = _failure(status, body)
            log.info("external file %s: http %s -> %s %s", ref.handle, status,
                     failure.status, failure.reason)
            raise failure
        raise ExternalFileError("unavailable", "the provider refused the sign-in")

    async def _token(self) -> str:
        """An access token for `DRIVE_SCOPE` from the shared credentials. No
        token (revoked sign-in, the scope not granted, the endpoint down) is
        `unavailable`: «I couldn't check», never «not shared»."""
        try:
            return await self._credentials.access_token(scope=DRIVE_SCOPE)
        except GoogleAuthError as err:
            raise ExternalFileError("unavailable", err.reason) from None
