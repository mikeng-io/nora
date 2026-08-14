import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.platform.discord.self_id import DiscordIdentityError, derive_identity


@pytest.fixture
async def me_server(aiohttp_server):
    body = {"id": "100000000000000100", "username": "nora", "bot": True}
    seen_auth: list[str | None] = []

    async def handler(request: web.Request) -> web.Response:
        seen_auth.append(request.headers.get("Authorization"))
        return web.json_response(body)

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)
    server.seen_auth = seen_auth
    return server


async def test_derive_identity_returns_the_bots_own_id_and_username(me_server):
    primary, identifiers = await derive_identity(
        "tok", base_url=str(me_server.make_url("")).rstrip("/")
    )

    assert primary == "100000000000000100"
    assert Identifier("discord", "user_id", "100000000000000100") in identifiers
    assert Identifier("discord", "username", "nora") in identifiers


async def test_derive_identity_authenticates_with_the_bot_token(me_server):
    await derive_identity("my-token", base_url=str(me_server.make_url("")).rstrip("/"))

    assert me_server.seen_auth == ["Bot my-token"]


async def test_derive_identity_raises_on_a_non_200(aiohttp_server):
    async def handler(request):
        return web.Response(status=401, text="unauthorized")

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)

    with pytest.raises(DiscordIdentityError):
        await derive_identity("bad-token", base_url=str(server.make_url("")).rstrip("/"))


async def test_derive_identity_raises_when_the_body_has_no_id(aiohttp_server):
    async def handler(request):
        return web.json_response({"username": "nora"})

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)

    with pytest.raises(DiscordIdentityError):
        await derive_identity("tok", base_url=str(server.make_url("")).rstrip("/"))
