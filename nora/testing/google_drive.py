"""`FakeDriveServer`: Google's OAuth token endpoint, Drive v3 and Sheets v4 as
an `httpx.MockTransport` handler, for `GoogleDriveSource`'s tests.

It behaves like the real servers on the paths the adapter takes, wrong calls
included, so a test cannot pass on a request Google would refuse:

- a request without the CURRENT access token is 401; a refresh with a revoked
  refresh token is 400 `invalid_grant`;
- a file nobody shared with the account, an unknown id, and a link-shared file
  asked for without its resource key are all the same bare 404 `notFound`
  (Drive does not say which);
- a file in the bin answers its metadata with `trashed: true`;
- `alt=media` on a Google-native file is 403 `fileNotDownloadable`,
  `files.export` on a stored file 403 `fileNotExportable`, and an export over
  Google's cap 403 `exportSizeLimitExceeded`;
- the Sheets API answers `valueRanges` in the order the ranges were asked.

Every request is kept in `requests`, so a test can count fetches.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

#: Spelled here, not imported: `nora.testing` sits below `nora.infra`.
DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
GSLIDES = "application/vnd.google-apps.presentation"


@dataclass
class DriveFile:
    name: str
    mime: str
    data: bytes = b""
    #: A native file's exports, by MIME type.
    exports: dict[str, bytes] = field(default_factory=dict)
    #: A Sheet's tabs, in order: title -> rows.
    tabs: dict[str, list[list[object]]] = field(default_factory=dict)
    shared: bool = True
    trashed: bool = False
    resource_key: str | None = None
    owner: str = "Jordan"
    modified: str = "2026-10-09T08:00:00.000Z"
    download_disabled: bool = False
    export_too_large: bool = False


def _reply(status: int, body: bytes | dict, **headers: str) -> httpx.Response:
    """A response whose body is still a STREAM, as a real one is (a
    `content=` response arrives already read, and a raw read of it fails)."""
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if isinstance(body, dict):
        headers.setdefault("Content-Type", "application/json")
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(data))


def _error(status: int, reason: str) -> httpx.Response:
    return _reply(status, {
        "error": {"code": status, "message": reason, "errors": [{"reason": reason}]},
    })


@dataclass
class FakeDriveServer:
    files: dict[str, DriveFile] = field(default_factory=dict)
    refresh_token: str = "refresh-1"  # noqa: S105 - a test value
    #: The token answer's `scope`: what the refresh token was granted.
    granted_scopes: str = "https://www.googleapis.com/auth/drive.readonly"
    revoked: bool = False
    down: bool = False
    rate_limited: bool = False
    requests: list[httpx.Request] = field(default_factory=list)
    #: Send file bodies gzip-compressed even when asked for `identity` (a
    #: server is allowed to): the adapter must inflate them bounded.
    gzip_bodies: bool = False
    #: The Sheets API not enabled in the OAuth client's Cloud project: it
    #: answers a 403 `PERMISSION_DENIED` whose detail says `SERVICE_DISABLED`.
    sheets_disabled: bool = False
    _issued: int = 0
    _minted: int = 0
    _token_scopes: dict[str, frozenset[str]] = field(default_factory=dict)

    def _body(self, data: bytes) -> httpx.Response:
        if self.gzip_bodies:
            return _reply(200, gzip.compress(data), **{"Content-Encoding": "gzip"})
        return _reply(200, data)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def token_calls(self) -> int:
        return sum(1 for r in self.requests if r.url.host == "oauth2.googleapis.com")

    def expire_tokens(self) -> None:
        """Every access token issued so far stops working (as at expiry)."""
        self._issued += 1

    def _live(self, request: httpx.Request) -> frozenset[str] | None:
        """The scopes of the request's bearer token, or None when it is not a
        token of the current generation."""
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token.startswith(f"access-{self._issued}-"):
            return None
        return self._token_scopes.get(token)

    def token_scopes_asked(self) -> list[str | None]:
        """Each token request's `scope=` form value (None: none sent)."""
        out = []
        for r in self.requests:
            if r.url.host == "oauth2.googleapis.com":
                form = {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
                out.append(form.get("scope"))
        return out

    def reads(self, file_id: str) -> int:
        """How many API requests named `file_id` (token calls excluded)."""
        return sum(1 for r in self.requests if f"/{file_id}" in r.url.path)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("unreachable", request=request)
        if request.url.host == "oauth2.googleapis.com":
            return self._token(request)
        scopes = self._live(request)
        if scopes is None:
            return _error(401, "authError")
        if DRIVE_READONLY not in scopes:
            return _error(403, "insufficientPermissions")
        if self.rate_limited:
            return _error(429, "rateLimitExceeded")
        path = request.url.path
        if request.url.host == "www.googleapis.com" and path.startswith("/drive/v3/files/"):
            return self._drive(request, path.removeprefix("/drive/v3/files/"))
        if request.url.host == "sheets.googleapis.com" and path.startswith("/v4/spreadsheets/"):
            return self._sheets(request, path.removeprefix("/v4/spreadsheets/"))
        return _error(404, "notFound")

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") != "refresh_token" or not form.get("client_secret"):
            return _reply(400, {"error": "invalid_request"})
        if self.revoked or form.get("refresh_token") != self.refresh_token:
            return _reply(400, {
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            })
        granted = frozenset(self.granted_scopes.split())
        # An explicit `scope` mints a token for that subset alone, as Google
        # does; asking beyond the grant is `invalid_scope`. No `scope`: the
        # whole grant.
        asked = frozenset(form["scope"].split()) if "scope" in form else granted
        if not asked <= granted:
            return _reply(400, {"error": "invalid_scope"})
        self._minted += 1
        token = f"access-{self._issued}-{self._minted}"
        self._token_scopes[token] = asked
        answer = {
            "access_token": token, "expires_in": 3599,
            "scope": " ".join(sorted(asked)),
            "token_type": "Bearer",
        }
        # Google compresses a token answer whenever the client accepts gzip
        # (httpx does by default), and also when `gzip_bodies` forces it.
        accepts = request.headers.get("Accept-Encoding", "")
        if self.gzip_bodies or "gzip" in accepts:
            return _reply(200, gzip.compress(json.dumps(answer).encode()),
                          **{"Content-Encoding": "gzip"})
        return _reply(200, answer)

    def _visible(self, request: httpx.Request, file_id: str) -> DriveFile | None:
        held = self.files.get(file_id)
        if held is None or not held.shared:
            return None
        if held.resource_key is not None:
            keys = request.headers.get("X-Goog-Drive-Resource-Keys", "")
            if f"{file_id}/{held.resource_key}" not in keys.split(","):
                return None
        return held

    def _drive(self, request: httpx.Request, rest: str) -> httpx.Response:
        file_id, _, verb = rest.partition("/")
        held = self._visible(request, file_id)
        if held is None:
            return _error(404, "notFound")
        params = request.url.params
        native = held.mime.startswith("application/vnd.google-apps.")
        if verb == "export":
            if not native:
                return _error(403, "fileNotExportable")
            if held.export_too_large:
                return _error(403, "exportSizeLimitExceeded")
            if held.download_disabled:
                return _error(403, "cannotExportFile")
            body = held.exports.get(params.get("mimeType", ""))
            if body is None:
                return _error(400, "badRequest")
            return self._body(body)
        if verb:
            return _error(404, "notFound")
        if params.get("alt") == "media":
            if native:
                return _error(403, "fileNotDownloadable")
            if held.download_disabled:
                return _error(403, "cannotDownloadFile")
            return self._body(held.data)
        meta: dict[str, object] = {
            "id": file_id, "name": held.name, "mimeType": held.mime,
            "modifiedTime": held.modified, "trashed": held.trashed,
            "owners": [{"displayName": held.owner}],
        }
        if not native:
            meta["size"] = str(len(held.data))
        return _reply(200, meta)

    def _sheets(self, request: httpx.Request, rest: str) -> httpx.Response:
        file_id, _, verb = rest.partition("/")
        if self.sheets_disabled:
            return _reply(403, {"error": {
                "code": 403, "status": "PERMISSION_DENIED",
                "message": "Google Sheets API has not been used in project 1 or it is disabled.",
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "SERVICE_DISABLED", "domain": "googleapis.com",
                }],
            }})
        held = self._visible(request, file_id)
        # The Sheets API does not read `X-Goog-Drive-Resource-Keys`: a sheet
        # reachable only with its link's resource key is a 404 here although
        # Drive answers for it.
        if held is None or held.mime != GSHEET or held.resource_key is not None:
            return _reply(404, {
                "error": {"code": 404, "message": "not found", "status": "NOT_FOUND"},
            })
        if not verb:
            return _reply(200, {"sheets": [
                {"properties": {"title": title, "sheetType": "GRID"}} for title in held.tabs
            ]})
        if verb == "values:batchGet":
            ranges = request.url.params.get_list("ranges")
            out = []
            for name in ranges:
                title = name[1:-1].replace("''", "'") if name.startswith("'") else name
                rows = held.tabs.get(title)
                if rows is None:
                    return _reply(400, {"error": {
                        "code": 400, "message": f"Unable to parse range: {name}",
                        "status": "INVALID_ARGUMENT",
                    }})
                out.append({"range": name, "majorDimension": "ROWS", "values": rows})
            return self._body(json.dumps({"valueRanges": out}).encode())
        return _error(404, "notFound")
