"""`ImageGenerator` behavioural contract, over `XaiImageGenerator` (DEC-137).

The contract every provider must honour — 3-state result, reason mapping,
fail-closed custody:

- `ok` carries an artifact (a URL or data URI) the caller can reply with.
- `nothing_found` means "the provider refused" or "the reference is not
  Nora-held" — NEVER an outage and NEVER an auth problem.
- `unavailable` carries a reason the tool maps to a plain sentence: quota /
  auth / rate_limited / down.

Live-credential calls are deliberately OUT (the live-credential gate F6
asked for in the adversarial review): the xAI lane is a real subscription,
and a CI that burned quota on every `make gate` would be a bill, not a
gate. Every path here runs against a local aiohttp `TestServer`, no
network.

The 403 ambiguity (design §7) is the centrepiece: xAI answers 403 for BOTH
quota exhaustion and entitlement failure. The adapter must read the body
and report the reason — never guess from the status alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import ImageReference, ImageSize
from nora.infra.image_gen.xai_adapter import XaiImageGenerator
from nora.infra.image_gen.xai_oauth import (
    OAuthState,
    XaiOAuth,
    XaiOAuthFlowError,
    device_flow_login,
)


class _CannedOAuth(XaiOAuth):
    """`access_token()` returns a canned token without touching the network
    or the state file — the image-endpoint contract is what is under test."""

    async def access_token(self) -> OAuthState:  # type: ignore[override]
        return OAuthState(status="ok", access_token="test-token")  # noqa: S106


@pytest.fixture
async def server_factory():
    """Yield a factory that spins up a local aiohttp server for one test's
    handler (the same zero-dependency pattern `test_honcho_card.py` uses)."""
    servers: list[TestServer] = []

    async def _make(handler) -> str:
        app = web.Application()
        app.router.add_post("/v1/images/generations", handler)
        app.router.add_post("/v1/images/edits", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("/")).rstrip("/")

    yield _make

    for server in servers:
        await server.close()


def _json(data, status: int = 200):
    return web.json_response(data, status=status)


# -- device-flow -----------------------------------------------------------

@pytest.fixture
async def device_server():
    """Local stand-in for xAI's OIDC discovery + token endpoints."""
    app = web.Application()
    started: dict = {}

    async def discovery(_request: web.Request) -> web.Response:
        return _json({"token_endpoint": str(server.make_url("/oauth2/token"))})

    async def device_code(_request: web.Request) -> web.Response:
        return _json(
            {
                "device_code": "dev-1",
                "user_code": "ABCD-1234",
                "verification_uri": "https://auth.x.ai/oauth2/device",
                "verification_uri_complete": "https://auth.x.ai/oauth2/device?code=ABCD-1234",
                "expires_in": started.get("expires_in", 900),
                "interval": started.get("interval", 1),
            }
        )

    async def token(_request: web.Request) -> web.Response:
        started["polls"] = started.get("polls", 0) + 1
        if started.get("always_pending"):
            return _json({"error": "authorization_pending"}, status=400)
        if started["polls"] <= 2:
            # first two polls: user hasn't approved yet
            return _json({"error": "authorization_pending"}, status=400)
        return _json({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})

    app.router.add_get("/.well-known/openid-configuration", discovery)
    app.router.add_post("/oauth2/device/code", device_code)
    app.router.add_post("/oauth2/token", token)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/")).rstrip("/"), started
    await server.close()


async def test_generate_ok_b64(server_factory):
    base = await server_factory(lambda req: _json({"data": [{"b64_json": "aGVsbG8="}]}))
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "ok"
    assert result.artifact
    assert result.artifact.startswith("data:image/png;base64,")
    assert result.provider == "xai"


async def test_generate_ok_url(server_factory):
    base = await server_factory(
        lambda req: _json({"data": [{"url": "https://example.com/i.png"}]})
    )
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "ok"
    assert result.artifact == "https://example.com/i.png"


async def test_generate_quota_403_classified_by_body(server_factory):
    base = await server_factory(
        lambda req: _json({"error": {"code": "quota_exceeded"}}, status=403)
    )
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "unavailable"
    assert result.reason == "quota"


async def test_generate_auth_403_classified_by_body(server_factory):
    base = await server_factory(
        lambda req: _json({"error": {"code": "entitlement_failed"}}, status=403)
    )
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "unavailable"
    assert result.reason == "auth"


async def test_generate_403_without_clear_body_is_generic(server_factory):
    """Fail-closed centrepiece: a 403 whose body does not say quota or auth
    must NOT be guessed — the caller says «I can't make images» rather than
    inventing a reason."""
    base = await server_factory(
        lambda req: _json({"error": {"code": "something_else"}}, status=403)
    )
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "unavailable"
    assert result.reason == "unavailable"


async def test_generate_429_is_rate_limited(server_factory):
    base = await server_factory(
        lambda req: _json({"error": {"code": "rate_limit"}}, status=429)
    )
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "unavailable"
    assert result.reason == "rate_limited"


async def test_generate_400_is_nothing_found(server_factory):
    """A 400 (prompt refused) is a provider verdict, NOT an outage — the
    three-state contract's middle state."""
    base = await server_factory(
        lambda req: _json({"error": {"message": "prompt invalid"}}, status=400)
    )
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "nothing_found"


async def test_generate_empty_prompt_is_nothing_found(server_factory):
    base = await server_factory(lambda req: _json({"data": []}))
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="   ")
    assert result.status == "nothing_found"
    assert result.reason == "empty_prompt"


async def test_reference_resolves_to_bytes_sends_edit(server_factory):
    """The custody contract's positive path: a resolver that finds the blob
    yields a multipart edit to /v1/images/edits."""
    captured: dict = {}

    async def handler(request: web.Request) -> web.Response:
        captured["path"] = request.path
        reader = await request.multipart()
        async for part in reader:
            if part.name == "prompt":
                captured["prompt"] = (await part.read()).decode()
        return _json({"data": [{"b64_json": "aGVsbG8="}]})

    base = await server_factory(handler)
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )

    async def resolver(marker: str) -> bytes | None:
        assert marker == "a3f2c1"
        return b"\x89PNG\r\n\x1a\nfake"

    result = await gen.generate(
        prompt="make it a watercolour",
        reference_images=[ImageReference(marker_code="a3f2c1")],
        reference_resolver=resolver,
    )
    assert result.status == "ok"
    assert captured["path"] == "/v1/images/edits"
    assert captured["prompt"] == "make it a watercolour"


async def test_reference_not_in_custody_is_nothing_found(server_factory):
    """Custody fail-closed: a marker that resolves to None is
    `nothing_found` — «I can't find that photo», never a fetch attempt and
    never an outage."""
    base = await server_factory(lambda req: _json({"data": []}))
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )

    async def resolver(marker: str) -> bytes | None:
        return None

    result = await gen.generate(
        prompt="edit this",
        reference_images=[ImageReference(marker_code="ffffff")],
        reference_resolver=resolver,
    )
    assert result.status == "nothing_found"
    assert result.reason == "unknown_reference"


async def test_reference_without_resolver_is_unavailable(server_factory):
    """Wiring gap: a reference with no resolver is `unavailable` (a wiring
    gap), never `nothing_found` (a lookup miss)."""
    base = await server_factory(lambda req: _json({"data": []}))
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(
        prompt="edit this",
        reference_images=[ImageReference(marker_code="a3f2c1")],
    )
    assert result.status == "unavailable"
    assert result.reason == "no_reference_resolver"


async def test_aspect_ratio_is_sent(server_factory):
    captured: dict = {}

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        captured["aspect"] = data.get("aspect_ratio")
        return _json({"data": [{"b64_json": "aGVsbG8="}]})

    base = await server_factory(handler)
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    await gen.generate(
        prompt="a cat", size=ImageSize(tier="large", aspect_ratio="16:9")
    )
    assert captured["aspect"] == "16:9"


async def test_device_flow_polls_token_endpoint_until_approved(device_server):
    base, started = device_server
    refresh = await device_flow_login(
        client_id="client-1",
        timeout_seconds=10.0,
        base_url=base,  # type: ignore[arg-type]
    )
    assert refresh == "rt-1"
    assert started["polls"] >= 3


async def test_device_flow_never_waits_forever(device_server):
    base, started = device_server
    started["always_pending"] = True
    started["expires_in"] = 3  # fast-expiring code
    with pytest.raises(XaiOAuthFlowError):
        await device_flow_login(
            client_id="client-1",
            timeout_seconds=10.0,
            base_url=base,  # type: ignore[arg-type]
        )
    assert started["polls"] > 0
