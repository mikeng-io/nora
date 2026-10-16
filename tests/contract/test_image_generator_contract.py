"""`ImageGenerator` behavioural contract, over `XaiImageGenerator` (DEC-137).

The contract every provider must honour — 3-state result, reason mapping,
fail-closed custody:

- `ok` carries an artifact (a URL or data URI) the caller can reply with.
- `nothing_found` means "the provider refused" or "the reference is not
  Nora-held" — NEVER an outage and NEVER an auth problem.
- `unavailable` carries a reason the tool maps to a plain sentence: quota /
  auth / rate_limited / down.

Live-credential calls are deliberately OUT: the xAI lane is a real
subscription, and a CI that burned quota on every `make gate` would be a bill,
not a gate. Every path here runs against a local aiohttp `TestServer`, no
network.

The 403 ambiguity is the centrepiece: xAI answers 403 for BOTH
quota exhaustion and entitlement failure. The adapter must read the body
and report the reason — never guess from the status alone.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import ImageReference, ImageSize
from nora.infra.image_gen import build_image_generator
from nora.infra.image_gen.openai_codex_adapter import (
    CODEX_MAX_REFERENCE_IMAGES,
    OpenAICodexImageGenerator,
)
from nora.infra.image_gen.openai_codex_oauth import CodexAuth
from nora.infra.image_gen.xai_adapter import XAI_MAX_REFERENCE_IMAGES, XaiImageGenerator
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
    yields a JSON edit to /v1/images/edits (xAI is JSON-only since
    grok-imagine-2.0 — multipart gets 415)."""
    captured: dict = {}

    async def handler(request: web.Request) -> web.Response:
        captured["path"] = request.path
        data = await request.json()
        captured["prompt"] = data["prompt"]
        captured["image"] = data["image"]
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
    assert captured["image"] == {"url": "data:image/png;base64,iVBORw0KGgpmYWtl"}


async def test_multi_reference_sends_images_array(server_factory):
    """Multi-image editing: two references travel as `images` array + the
    prompt gains <IMAGE_0>, <IMAGE_1> placeholders (xAI multi-image edit,
    grok-imagine-2.0)."""
    captured: dict = {}

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        captured["images"] = data["images"]
        captured["prompt"] = data["prompt"]
        return _json({"data": [{"b64_json": "aGVsbG8="}]})

    base = await server_factory(handler)
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )

    async def resolver(marker: str) -> bytes | None:
        return b"\x89PNG\r\n\x1a\nfake"

    result = await gen.generate(
        prompt="compose these together",
        reference_images=[
            ImageReference(marker_code="a3f2c1"),
            ImageReference(marker_code="b4e5d6"),
        ],
        reference_resolver=resolver,
    )
    assert result.status == "ok"
    assert len(captured["images"]) == 2
    assert captured["images"][0] == {"url": "data:image/png;base64,iVBORw0KGgpmYWtl"}
    assert "IMAGE_0" in captured["prompt"]
    assert "IMAGE_1" in captured["prompt"]


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


# -- refresh path ------------------------------------------------------------

@pytest.fixture
async def oauth_server():
    """Local stand-in for the OIDC discovery + refresh token endpoints."""
    app = web.Application()
    state: dict = {}

    async def discovery(_request: web.Request) -> web.Response:
        return _json({"token_endpoint": str(server.make_url("/oauth2/token"))})

    async def token(request: web.Request) -> web.Response:
        state["refreshes"] = state.get("refreshes", 0) + 1
        data = await request.post()
        state["grant_type"] = data.get("grant_type")
        state["sent_refresh"] = data.get("refresh_token")
        if state.get("behavior") == "403":
            return _json({"error": {"code": "challenge"}}, status=403)
        if state.get("behavior") == "401":
            return _json({"error": "invalid_grant"}, status=401)
        body: dict = {"access_token": "at-new", "expires_in": state.get("expires_in", 7200)}
        if state.get("rotate"):
            body["refresh_token"] = "rt-rotated"  # noqa: S105 — fake test token
        return _json(body)

    app.router.add_get("/.well-known/openid-configuration", discovery)
    app.router.add_post("/oauth2/token", token)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/")).rstrip("/"), state
    await server.close()


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _oauth(tmp_path: Path, base: str, refresh: str = "rt-boot", **kw):
    return XaiOAuth(
        refresh_token=refresh,
        state_file=tmp_path / "xai-oauth.json",
        timeout_seconds=5.0,
        issuer=base,
        **kw,
    )


async def test_refresh_success_writes_state_atomic(tmp_path, oauth_server):
    base, state = oauth_server
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "ok"
    assert result.access_token == "at-new"  # noqa: S105 — fake test token
    assert state["refreshes"] == 1
    assert state["grant_type"] == "refresh_token"
    assert state["sent_refresh"] == "rt-boot"
    # state file exists with 0o600 and is JSON
    sf = tmp_path / "xai-oauth.json"
    assert sf.exists()
    assert (sf.stat().st_mode & 0o777) == 0o600
    data = json.loads(sf.read_text())
    assert data["access_token"] == "at-new"  # noqa: S105 — fake test token
    assert data["expires_at"] > 0


async def test_refresh_rotation_persisted(tmp_path, oauth_server):
    base, state = oauth_server
    state["rotate"] = True
    state["expires_in"] = 1  # short-lived: forces the second call to re-refresh
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "ok"
    sf = tmp_path / "xai-oauth.json"
    data = json.loads(sf.read_text())
    assert data["refresh_token"] == "rt-rotated"  # noqa: S105 — fake test token
    # memory adopted: next refresh uses the rotated token
    state["rotate"] = False
    await oauth.access_token()
    assert state["sent_refresh"] == "rt-rotated"


async def test_expired_state_adopts_rotated_refresh_before_refresh(tmp_path, oauth_server):
    """xAI rotates refresh tokens on use. With an EXPIRED access
    token in the state file plus a rotated (newer) refresh token there, the
    lane must refresh with the ROTATED token — never the stale `.env`/boot
    one — so a state-mutating lane heals without a re-login. The `.env`
    token (rt-boot) is dead; the rotated one (rt-rotated) is live."""
    base, state = oauth_server
    state["rotate"] = True
    sf = tmp_path / "xai-oauth.json"
    sf.write_text(
        json.dumps({
            "access_token": "at-expired",
            "expires_at": 1,               # long-expired: fresh check fails
            "refresh_token": "rt-rotated",
            "bootstrap_sha256": _sha("rt-boot"),  # this chain grew from rt-boot
        }),
        encoding="utf-8",
    )
    oauth = _oauth(tmp_path, base)  # boot token rt-boot is the DEAD .env token
    result = await oauth.access_token()
    assert result.status == "ok"
    assert result.access_token == "at-new"  # noqa: S105 — fake test token
    # The rotated token from state was adopted and used, not the dead boot token
    assert state["sent_refresh"] == "rt-rotated"


async def test_refresh_403_is_cloudflare(tmp_path, oauth_server):
    base, state = oauth_server
    state["behavior"] = "403"
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "unavailable"
    assert result.reason == "cloudflare"


async def test_refresh_401_is_auth(tmp_path, oauth_server):
    base, state = oauth_server
    state["behavior"] = "401"
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "unavailable"
    assert result.reason == "auth"


async def test_refresh_missing_expires_in_defaults_300(tmp_path, oauth_server):
    base, state = oauth_server
    state["expires_in"] = 0  # missing/zero → conservative default
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "ok"
    # immediate re-call should NOT hammer the endpoint
    result2 = await oauth.access_token()
    assert state["refreshes"] == 1
    assert result2.status == "ok"


async def test_refresh_corrupt_state_recovers(tmp_path, oauth_server):
    base, state = oauth_server
    sf = tmp_path / "xai-oauth.json"
    sf.write_text("{corrupt-json", encoding="utf-8")
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "ok"
    assert state["refreshes"] == 1  # recovered from .env, not from corrupt file


async def test_refresh_single_flight(tmp_path, oauth_server):
    base, state = oauth_server
    oauth = _oauth(tmp_path, base)
    r1, r2 = await asyncio.gather(
        oauth.access_token(), oauth.access_token(),
    )
    assert r1.status == "ok" and r2.status == "ok"
    assert state["refreshes"] == 1  # single flight: concurrent callers share


async def test_refresh_boot_precedence_uses_state_file(tmp_path, oauth_server):
    """Boot precedence: a valid state file is used as-is; the .env
    refresh token is NOT hit on every boot."""
    base, state = oauth_server
    sf = tmp_path / "xai-oauth.json"
    sf.write_text(
        json.dumps(
            {
                "access_token": "cached-token", "expires_at": 2_000_000_000,
                "refresh_token": "rt-rotated", "bootstrap_sha256": _sha("rt-boot"),
            },
        ),
        encoding="utf-8",
    )
    oauth = _oauth(tmp_path, base)
    result = await oauth.access_token()
    assert result.status == "ok"
    assert result.access_token == "cached-token"  # noqa: S105 — fake test token
    assert "refreshes" not in state


async def test_resolution_is_sent(server_factory):
    captured: dict = {}

    async def handler(request: web.Request) -> web.Response:
        data = await request.json()
        captured["resolution"] = data.get("resolution")
        return _json({"data": [{"b64_json": "aGVsbG8="}]})

    base = await server_factory(handler)
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    await gen.generate(prompt="a cat", size=ImageSize(tier="large", aspect_ratio="1:1"))
    assert captured["resolution"] == "2k"


async def test_drop_is_not_involved(tmp_path):
    """The generate lane does NOT use `drop` at all — the
    adapter returns raw bytes for Nora's OWN custody (rustfs), never uploads
    to an external host. There is no drop parameter to wire, and the raw
    bytes ride along on the result."""

    async def _ok(_request: web.Request) -> web.Response:
        return _json({"data": [{"b64_json": "aGVsbG8="}]})

    app = web.Application()
    app.router.add_post("/v1/images/generations", _ok)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")

    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    result = await gen.generate(prompt="a cat")
    assert result.status == "ok"
    assert result.raw is not None
    assert result.raw == b"hello"
    assert result.media_type == "image/png"


# -- the reference limit is the provider's (DEC-307) ------------------------

_PNG = b"\x89PNG\r\n\x1a\n"


class _CannedCodexAuth:
    """The one call the Codex adapter makes on its lane, canned: the image
    endpoint is under test here, not the rotating refresh chain."""

    async def access_token(self, *, rejected: str = "") -> CodexAuth:
        return CodexAuth(status="ok", access_token="t", account_id="a")  # noqa: S106


def _refs(n: int) -> list[ImageReference]:
    return [ImageReference(f"{i:06x}") for i in range(n)]


async def _resolve(code: str) -> bytes:
    return _PNG + code.encode()


def _uri(i: int) -> str:
    return "data:image/png;base64," + base64.b64encode(_PNG + f"{i:06x}".encode()).decode()


async def test_xai_sends_every_reference_up_to_its_documented_limit(server_factory):
    """xAI documents five source images per edit; all five travel, in order,
    in `images` (a single one would travel in `image`)."""
    captured: list[dict] = []

    async def _edits(request: web.Request) -> web.Response:
        captured.append(await request.json())
        return _json({"data": [{"b64_json": "aGVsbG8="}]})

    base = await server_factory(_edits)
    gen = XaiImageGenerator(
        oauth=_CannedOAuth(refresh_token="rt", state_file=Path("no-state")),  # noqa: S106
        base_url=base, timeout_seconds=5.0,
    )
    assert gen.max_reference_images == XAI_MAX_REFERENCE_IMAGES == 5
    result = await gen.generate(
        prompt="all of us", reference_images=_refs(5), reference_resolver=_resolve,
    )
    assert result.status == "ok"
    assert [img["url"] for img in captured[0]["images"]] == [_uri(i) for i in range(5)]


async def test_codex_sends_every_reference_up_to_its_documented_limit():
    """OpenAI documents sixteen input images for gpt-image; all sixteen travel,
    in order, as `input_image` parts after the prompt."""
    captured: list[dict] = []
    item = {"type": "image_generation_call", "status": "completed",
            "result": base64.b64encode(_PNG + b"\x00" * 32).decode()}
    stream = b"data: " + json.dumps(
        {"type": "response.output_item.done", "item": item}).encode() + b"\n\n"

    async def responses(request: web.Request) -> web.Response:
        captured.append(await request.json())
        return web.Response(body=stream, content_type="text/event-stream")

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/responses", responses)
    server = TestServer(app)
    await server.start_server()
    try:
        gen = OpenAICodexImageGenerator(
            oauth=_CannedCodexAuth(),  # type: ignore[arg-type]
            base_url=str(server.make_url("/")).rstrip("/"), timeout_seconds=5.0,
        )
        assert gen.max_reference_images == CODEX_MAX_REFERENCE_IMAGES == 16
        result = await gen.generate(
            prompt="all of us", reference_images=_refs(16), reference_resolver=_resolve,
        )
    finally:
        await server.close()
    assert result.status == "ok"
    parts = captured[0]["input"][0]["content"]
    assert parts[0]["type"] == "input_text"
    assert [p["image_url"] for p in parts[1:]] == [_uri(i) for i in range(16)]


@pytest.mark.parametrize(
    ("section", "vendor"),
    [("xai", XAI_MAX_REFERENCE_IMAGES), ("openai_codex", CODEX_MAX_REFERENCE_IMAGES)],
)
def test_an_operator_override_only_lowers_the_limit(section, vendor):
    """`max_reference_images` in a provider's config section lowers its limit
    and never raises it: the config bound IS the adapter's vendor constant, so
    neither can move without the other going red here."""
    from pydantic import ValidationError

    from nora.config import ImageGenConfig

    assert getattr(ImageGenConfig(), section).max_reference_images is None
    ok = ImageGenConfig.model_validate({section: {"max_reference_images": vendor}})
    assert getattr(ok, section).max_reference_images == vendor
    for bad in (0, vendor + 1):
        with pytest.raises(ValidationError):
            ImageGenConfig.model_validate({section: {"max_reference_images": bad}})

    def build(**extra):
        return build_image_generator(
            provider=section, refresh_token="rt",  # noqa: S106
            state_file=Path("no-state"), **extra,
        )

    assert build().max_reference_images == vendor
    assert build(max_reference_images=2).max_reference_images == 2
    with pytest.raises(ValueError, match="max_reference_images"):
        build(max_reference_images=vendor + 1)
