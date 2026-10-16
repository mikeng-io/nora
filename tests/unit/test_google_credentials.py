"""`GoogleCredentials`: Nora's ONE Google OAuth client and refresh token,
shared by every Google service. One refresh token serves every consumer, each
with its own downscoped access token; a consumer whose scope the token was not
granted is unavailable on its own; the secrets reach no adapter and no log."""

from __future__ import annotations

import inspect
import logging

import httpx
import pytest

from nora.domain.external_files import ExternalFileError, recognise
from nora.infra.external_files.google_drive import DRIVE_SCOPE, GoogleDriveSource
from nora.infra.google import scopes
from nora.infra.google.auth import GoogleAuthError, GoogleCredentials
from nora.obs.metrics import Metrics
from nora.testing.google_drive import GDOC, DriveFile, FakeDriveServer

SECRET = "client-secret-7f3a"  # noqa: S105 - a test value
CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"
ID = "1SharedIdentity0000000000"
URL = f"https://docs.google.com/document/d/{ID}/edit"


def _server(granted: str) -> FakeDriveServer:
    return FakeDriveServer(
        files={ID: DriveFile("Plan", GDOC, exports={"text/markdown": b"hello"})},
        refresh_token="refresh-9c1e",  # noqa: S106 - a test value
        granted_scopes=granted,
    )


def _credentials(server: FakeDriveServer, metrics: Metrics | None = None) -> GoogleCredentials:
    return GoogleCredentials(
        client_id="cid", client_secret=SECRET, refresh_token=server.refresh_token,
        metrics=metrics or Metrics(), transport=server.transport,
    )


async def test_two_consumers_share_one_refresh_and_one_token():
    """One refresh token for every consumer; each scope set gets its OWN
    access token, minted once and reused until it expires."""
    server = _server(f"{DRIVE_SCOPE} {CALENDAR}")
    creds = _credentials(server)
    drive = await creds.access_token(scope=DRIVE_SCOPE)
    calendar = await creds.access_token(scope=CALENDAR)
    assert drive != calendar
    assert server.token_scopes_asked() == [None, DRIVE_SCOPE, CALENDAR]
    assert await creds.access_token(scope=DRIVE_SCOPE) == drive
    # And a Drive adapter on the same credentials refreshes nothing more.
    source = GoogleDriveSource(credentials=creds, transport=server.transport)
    assert (await source.read(ref=recognise(URL), part=1)).text == "hello"
    assert server.token_calls() == 3


async def test_the_drive_token_carries_drive_readonly_alone(monkeypatch):
    """Read-only holds in the CREDENTIAL. Another
    service's declared write scope in the same grant never rides on the Drive
    adapter's token."""
    events = "https://www.googleapis.com/auth/calendar.events"
    monkeypatch.setitem(scopes.CONSUMER_SCOPES, "calendar.write", events)
    server = _server(f"{DRIVE_SCOPE} {events}")
    source = GoogleDriveSource(credentials=_credentials(server), transport=server.transport)
    assert (await source.read(ref=recognise(URL), part=1)).text == "hello"
    assert server.token_scopes_asked() == [None, DRIVE_SCOPE]
    api = [r for r in server.requests if r.url.host != "oauth2.googleapis.com"]
    tokens = {r.headers["Authorization"].removeprefix("Bearer ") for r in api}
    assert [server._token_scopes[t] for t in tokens] == [frozenset({DRIVE_SCOPE})]


async def test_a_grant_with_an_undeclared_write_scope_is_refused(caplog):
    """A refresh token that could write (full `drive`, declared by no
    service) is refused for every consumer: no token is minted for one."""
    full = "https://www.googleapis.com/auth/drive"
    server = _server(f"{DRIVE_SCOPE} {full}")
    creds = _credentials(server)
    with caplog.at_level(logging.ERROR, logger="nora.google"):
        for _ in range(2):
            with pytest.raises(GoogleAuthError) as raised:
                await creds.access_token(scope=DRIVE_SCOPE)
            assert raised.value.reason == "undeclared_write_scope"
    assert server.token_scopes_asked() == [None]
    assert full in caplog.text and caplog.text.count("can write") == 1


def test_read_only_and_write_scopes_are_told_apart():
    assert scopes.is_read_only(DRIVE_SCOPE) and scopes.is_read_only("openid")
    assert not scopes.is_read_only("https://www.googleapis.com/auth/drive")
    assert not scopes.is_read_only("https://www.googleapis.com/auth/drive.file")
    assert scopes.undeclared_write_scopes(frozenset({DRIVE_SCOPE, CALENDAR})) == ()


async def test_a_scope_not_granted_fails_that_consumer_closed_and_no_other(caplog):
    server = _server(CALENDAR)  # the token lacks drive.readonly
    creds = _credentials(server)
    with caplog.at_level(logging.INFO, logger="nora.google"):
        with pytest.raises(GoogleAuthError) as raised:
            await creds.access_token(scope=DRIVE_SCOPE)
        assert raised.value.reason == "scope_not_granted"
        assert await creds.access_token(scope=CALENDAR)
        with pytest.raises(GoogleAuthError):
            await creds.access_token(scope=DRIVE_SCOPE)
    assert DRIVE_SCOPE in caplog.text and "google_auth.py" in caplog.text
    assert caplog.text.count(DRIVE_SCOPE) == 1  # said once, not per call
    # Refused from the grant already read: no Drive token is ever asked for.
    assert server.token_scopes_asked() == [None, CALENDAR]


async def test_the_drive_adapter_without_its_scope_is_unavailable_and_calls_nothing():
    server = _server(CALENDAR)
    source = GoogleDriveSource(credentials=_credentials(server), transport=server.transport)
    with pytest.raises(ExternalFileError) as raised:
        await source.describe(ref=recognise(URL))
    assert (raised.value.status, raised.value.reason) == ("unavailable", "scope_not_granted")
    assert [r for r in server.requests if r.url.host != "oauth2.googleapis.com"] == []


async def test_an_answer_with_no_scope_grants_nothing():
    server = _server("")
    with pytest.raises(GoogleAuthError) as raised:
        await _credentials(server).access_token(scope=DRIVE_SCOPE)
    assert raised.value.reason == "scope_not_granted"


def test_an_adapter_never_holds_or_takes_the_secrets():
    params = set(inspect.signature(GoogleDriveSource).parameters)
    assert not params & {"client_id", "client_secret", "refresh_token"}
    server = _server(DRIVE_SCOPE)
    creds = _credentials(server)
    source = GoogleDriveSource(credentials=creds, transport=server.transport)
    own = {k: v for k, v in vars(source).items() if v is not creds}
    assert SECRET not in repr(own) and server.refresh_token not in repr(own)
    assert SECRET not in repr(creds) and server.refresh_token not in repr(creds)
    public = {name: getattr(creds, name) for name in dir(creds) if not name.startswith("_")}
    assert SECRET not in repr(public) and server.refresh_token not in repr(public)


@pytest.mark.parametrize("trouble", ["ok", "revoked", "down"])
async def test_no_secret_or_token_reaches_a_log(caplog, trouble):
    server = _server(CALENDAR)
    server.revoked = trouble == "revoked"
    metrics = Metrics()
    creds = _credentials(server, metrics)
    with caplog.at_level(logging.DEBUG):
        if trouble == "down":
            server.down = True
        with pytest.raises(GoogleAuthError):
            await creds.access_token(scope=DRIVE_SCOPE)
    for secret in (SECRET, server.refresh_token, "access-0"):
        assert secret not in caplog.text
    if trouble == "revoked":
        assert metrics.get("nora_google_invalid_grant_total") == 1


async def test_a_gzipped_token_answer_is_read():
    """Google gzips a token answer whenever the client accepts gzip; the raw bytes
    must not be parsed as JSON. A compressed answer must still yield a token."""
    server = _server(DRIVE_SCOPE)
    server.gzip_bodies = True
    assert await _credentials(server).access_token(scope=DRIVE_SCOPE)


async def test_the_token_request_asks_for_an_uncompressed_answer():
    seen: list[str] = []
    server = _server(DRIVE_SCOPE)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            seen.append(request.headers.get("Accept-Encoding", ""))
        return server.handle(request)

    creds = GoogleCredentials(
        client_id="cid", client_secret=SECRET, refresh_token=server.refresh_token,
        metrics=Metrics(), transport=httpx.MockTransport(handler),
    )
    assert await creds.access_token(scope=DRIVE_SCOPE)
    assert seen and all(h == "identity" for h in seen)
