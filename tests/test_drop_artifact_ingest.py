"""`DropArtifactIngest` (`nora.infra.drop`) against an in-process aiohttp
double standing in for `drop`'s internal listener — the same shape
`tests/test_shortener.py` uses for `LinkShortener`, chosen so this suite never
needs a live `drop` container (that verification is a separate, docker-based
step; see `deploy/drop/tests/` and this task's own manual verification, not
the checked-in suite).

Unlike `LinkShortener`, every failure here must RAISE rather than degrade to
`None` — `ArtifactIngest.register`'s own contract, exercised directly by
`tests/contract/test_artifact_ingest_contract.py` alongside
`FakeArtifactIngest`. This file additionally checks the request `drop` itself
actually receives (multipart fields, bearer header), which the contract suite
does not — that shape is this implementation's own, not part of the port.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import ArtifactRegistration
from nora.infra.drop import DropArtifactIngest

TOKEN = "test-drop-token"  # noqa: S105


class _Drop:
    def __init__(self) -> None:
        self.mode = "ok"  # ok | 403 | non_json | malformed | hang
        self.received: list[dict[str, object]] = []

    async def register(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "forbidden"}, status=403)
        if self.mode == "403":
            return web.json_response({"error": "forbidden"}, status=403)
        if self.mode == "non_json":
            return web.Response(text="<html>nope</html>", status=200,
                                content_type="text/html")
        if self.mode == "hang":
            await asyncio.sleep(30)

        reader = await request.multipart()
        fields: dict[str, object] = {}
        async for part in reader:
            if part.name == "file":
                fields["file"] = await part.read(decode=False)
                fields["filename"] = part.filename
                fields["content_type"] = part.headers.get("Content-Type")
            else:
                fields[part.name] = (await part.read(decode=True)).decode()
        self.received.append(fields)

        if self.mode == "malformed":
            return web.json_response({"nonsense": True})
        return web.json_response(
            {"artifact_id": "art_000123", "url": "https://drop.nora.example.net/d/abc1234"}
        )


@pytest.fixture
async def drop():
    fake = _Drop()
    app = web.Application()
    app.router.add_post("/api/artifacts", fake.register)
    server = TestServer(app)
    await server.start_server()
    yield fake, str(server.make_url("/")).rstrip("/")
    await server.close()


def _client(base, *, token=TOKEN, timeout=5.0):
    return DropArtifactIngest(base_url=base, token=token, timeout_seconds=timeout)


async def test_a_successful_register_returns_an_artifact_registration(drop):
    _fake, base = drop
    reg = await _client(base).register(
        data=b"hello world", title="Report", media_type="text/plain",
    )
    assert reg == ArtifactRegistration(
        artifact_id="art_000123", url="https://drop.nora.example.net/d/abc1234",
    )


async def test_the_request_carries_the_given_fields_as_multipart(drop):
    fake, base = drop
    await _client(base).register(
        data=b"payload-bytes", title="My Title", media_type="application/pdf",
        expires_at=1_786_320_000_000,
    )
    assert len(fake.received) == 1
    sent = fake.received[0]
    assert sent["file"] == b"payload-bytes"
    assert sent["content_type"] == "application/pdf"
    assert sent["title"] == "My Title"
    assert sent["media_type"] == "application/pdf"
    assert sent["expires_at"] == "1786320000000"


async def test_expires_at_defaults_to_absent_not_a_sent_none(drop):
    """`None` means "apply drop's own default" — the port's own docstring —
    which this class expresses by omitting the field entirely, not by
    serialising the word "None" onto the wire."""
    fake, base = drop
    await _client(base).register(data=b"x", title="X", media_type="text/plain")
    assert "expires_at" not in fake.received[0]


async def test_a_wrong_token_raises(drop):
    _fake, base = drop
    with pytest.raises(ConnectionError):
        await _client(base, token="wrong").register(  # noqa: S106
            data=b"x", title="X", media_type="text/plain",
        )


async def test_a_non_json_answer_raises(drop):
    fake, base = drop
    fake.mode = "non_json"
    with pytest.raises(ConnectionError):
        await _client(base).register(data=b"x", title="X", media_type="text/plain")


async def test_a_malformed_json_answer_raises(drop):
    fake, base = drop
    fake.mode = "malformed"
    with pytest.raises(ConnectionError):
        await _client(base).register(data=b"x", title="X", media_type="text/plain")


async def test_a_hung_drop_times_out_to_a_raise(drop):
    fake, base = drop
    fake.mode = "hang"
    with pytest.raises(ConnectionError):
        await _client(base, timeout=0.3).register(
            data=b"x", title="X", media_type="text/plain",
        )


async def test_an_unreachable_drop_raises():
    with pytest.raises(ConnectionError):
        await _client("http://127.0.0.1:1").register(
            data=b"x", title="X", media_type="text/plain",
        )
