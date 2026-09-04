"""`nora.platform.whatsapp.self_id.derive_identity` — the bot's own id forms
off the bridge's `GET /self`, mirroring `tests/test_discord_self_id.py`'s
hard-fail shape."""

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.platform.whatsapp.self_id import WhatsAppIdentityError, derive_identity


@pytest.fixture
async def self_server(aiohttp_server):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"phone": "85251112233", "lid": "11122233344455"})

    app = web.Application()
    app.router.add_get("/self", handler)
    return await aiohttp_server(app)


async def test_derive_identity_returns_the_phone_form_and_both_identifiers(self_server):
    primary, identifiers = await derive_identity(
        str(self_server.make_url("")).rstrip("/")
    )

    assert primary == "+85251112233"
    # The phone in "+<digits>" form — the same shape the parser's
    # `sender_id`/mention values take — and the lid as bare digits. Both are
    # what `registry.self_aliases()` membership-tests a mention against.
    assert identifiers == frozenset({
        Identifier("whatsapp", "phone", "+85251112233"),
        Identifier("whatsapp", "lid", "11122233344455"),
    })


async def test_a_bridge_that_reports_no_lid_yields_the_phone_alone(aiohttp_server):
    async def handler(request):
        return web.json_response({"phone": "85251112233", "lid": None})

    app = web.Application()
    app.router.add_get("/self", handler)
    server = await aiohttp_server(app)

    primary, identifiers = await derive_identity(str(server.make_url("")).rstrip("/"))

    assert primary == "+85251112233"
    assert identifiers == frozenset({Identifier("whatsapp", "phone", "+85251112233")})


async def test_a_503_while_the_socket_is_down_raises(aiohttp_server):
    """`GET /self` answers 503 `{"error": "not_connected"}` until the
    WhatsApp socket opens. Hard-fail, Discord's shape: there is no R-17
    multi-call degradation story here, so a failed derivation is a startup
    error, not a counted partial answer."""
    async def handler(request):
        return web.json_response({"error": "not_connected"}, status=503)

    app = web.Application()
    app.router.add_get("/self", handler)
    server = await aiohttp_server(app)

    with pytest.raises(WhatsAppIdentityError):
        await derive_identity(str(server.make_url("")).rstrip("/"))


async def test_a_body_with_no_usable_phone_raises(aiohttp_server):
    async def handler(request):
        return web.json_response({"lid": "11122233344455"})

    app = web.Application()
    app.router.add_get("/self", handler)
    server = await aiohttp_server(app)

    with pytest.raises(WhatsAppIdentityError):
        await derive_identity(str(server.make_url("")).rstrip("/"))
