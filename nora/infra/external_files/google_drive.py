"""`GoogleDriveSource`: the first `ExternalFilesSource`, Google Drive read-only
(design/external-files/00-design.md §4, build step S2).

Nora's own account (`nora@67.zone`), signed in once with
`scripts/external_files_auth.py` under ONE scope, `drive.readonly`: nothing
here can write, comment or share, and the Sheets reads accept that scope. The
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
rather than trusting an earlier `describe`. The ONLY thing held between calls
is the OAuth ACCESS token, in memory until its own expiry: a credential, not
anything fetched through a link. The refresh token and client secret come in
already resolved from the environment (DEC-8) and are never logged; neither is
a file id (a log line names the six-hex handle).

**How a failure reads** (`ExternalFileError.status`, reason code):

- 404 (Drive does not say whether the file exists): `refused`, `NOT_SHARED`.
  It is the common case, a link nobody shared with her, and the fix is the
  same either way: share it, or send the live link.
- 403 for a permission reason: `refused`, `NOT_SHARED`; `cannotDownloadFile`
  and its kin: `refused`, `NO_DOWNLOAD`; `exportSizeLimitExceeded` (Google's
  10 MB export cap) or more than `max_bytes` downloaded: `refused`,
  `TOO_LARGE`; a rate limit, an API not enabled: `unavailable`.
- `trashed`: `nothing_found`, `DELETED`.
- A refresh refused as `invalid_grant` (revoked, expired, or a Testing-mode
  consent screen's 7-day expiry): `unavailable`, logged as an error naming the
  fix and counted on `nora_external_files_invalid_grant_total`.
- 429, 5xx, a transport error, a body that is not JSON: `unavailable`.

Never raises anything but `ExternalFileError`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
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

from .text import docx_text, pages, pdf_text, plain_text, pptx_text, table_text, xlsx_text

log = logging.getLogger("nora.external_files")

__all__ = ["DRIVE_SCOPE", "MAX_BYTES", "PART_CHARS", "GoogleDriveSource"]


class Counter(Protocol):
    """`nora.obs.metrics.Metrics` as this module uses it; injected, since
    `nora.infra` sits below `nora.obs` in the layers contract."""

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...


#: The one scope the sign-in asks for (`scripts/external_files_auth.py`).
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
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
#: Seconds before an access token's stated expiry that it is refreshed.
_EXPIRY_MARGIN = 60.0

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


class GoogleDriveSource:
    """One process-lifetime adapter, built by the composition root under
    `[external_files]` when its three environment variables are set."""

    name = "google_drive"

    def __init__(
        self, *, client_id: str, client_secret: str, refresh_token: str,
        metrics: Counter, part_chars: int = PART_CHARS, max_bytes: int = MAX_BYTES,
        timeout_seconds: float = 20.0, transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._metrics = metrics
        self._part_chars = part_chars
        self._max_bytes = max_bytes
        self._timeout = httpx.Timeout(timeout_seconds)
        # The seam a test drives (`httpx.MockTransport`); None is real HTTPS.
        self._transport = transport
        self._clock = clock
        # The access token and when to stop using it: a credential held in
        # memory, never anything read through a link (module docstring).
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

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
            headers = {"Authorization": f"Bearer {await self._token()}"}
            if ref.resource_key:
                headers["X-Goog-Drive-Resource-Keys"] = f"{ref.file_id}/{ref.resource_key}"
            try:
                async with (
                    self._client() as client,
                    client.stream("GET", url, params=params, headers=headers) as resp,
                ):
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > self._max_bytes:
                            raise ExternalFileError("refused", TOO_LARGE)
                    status = resp.status_code
            except ExternalFileError:
                raise
            except Exception as exc:
                log.warning("external file %s: provider unreachable: %s", ref.handle,
                            type(exc).__name__)
                raise ExternalFileError("unavailable", "the provider could not be reached") from exc
            if status == 200:
                return bytes(body)
            if status == 401 and attempt == 1:
                self._access = None
                continue
            failure = _failure(status, bytes(body))
            log.info("external file %s: http %s -> %s %s", ref.handle, status,
                     failure.status, failure.reason)
            raise failure
        raise ExternalFileError("unavailable", "the provider refused the sign-in")

    async def _token(self) -> str:
        """The access token, refreshed from the refresh token when it is
        missing or about to expire. In memory only."""
        async with self._lock:
            if self._access is not None and self._clock() < self._expires_at:
                return self._access
            form = {
                "grant_type": "refresh_token", "refresh_token": self._refresh_token,
                "client_id": self._client_id, "client_secret": self._client_secret,
            }
            try:
                async with self._client() as client:
                    resp = await client.post(TOKEN_URL, data=form)
                payload = resp.json()
            except Exception as exc:
                log.warning("external files: token refresh failed: %s", type(exc).__name__)
                raise ExternalFileError(
                    "unavailable", "the sign-in could not be refreshed",
                ) from exc
            if not isinstance(payload, dict):
                payload = {}
            if resp.status_code == 200 and payload.get("access_token"):
                self._access = str(payload["access_token"])
                expires_in = payload.get("expires_in", 3600)
                seconds = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
                self._expires_at = self._clock() + max(0.0, seconds - _EXPIRY_MARGIN)
                return self._access
            error = str(payload.get("error", ""))
            if error == "invalid_grant":
                self._metrics.inc("nora_external_files_invalid_grant_total", provider=self.name)
                log.error(
                    "external files: Google refused the refresh token (invalid_grant: %s). "
                    "The sign-in is revoked or expired; run scripts/external_files_auth.py "
                    "and replace the refresh token in .env",
                    str(payload.get("error_description", ""))[:200],
                )
                raise ExternalFileError("unavailable", "invalid_grant")
            log.warning("external files: token refresh answered %s %s",
                        resp.status_code, error[:80])
            raise ExternalFileError("unavailable", "the sign-in could not be refreshed")
