import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.platform.discord.self_id import DiscordIdentityError, derive_identity, render_handle


@pytest.fixture
async def me_server(aiohttp_server):
    body = {
        "id": "100000000000000100", "username": "nora", "discriminator": "1234",
        "bot": True,
    }
    seen_auth: list[str | None] = []

    async def handler(request: web.Request) -> web.Response:
        seen_auth.append(request.headers.get("Authorization"))
        return web.json_response(body)

    app = web.Application()
    app.router.add_get("/users/@me", handler)
    server = await aiohttp_server(app)
    server.seen_auth = seen_auth
    return server


async def test_derive_identity_returns_the_bots_own_id_and_never_its_username(me_server):
    primary, identifiers, _handle = await derive_identity(
        "tok", base_url=str(me_server.make_url("")).rstrip("/")
    )

    assert primary == "100000000000000100"
    assert Identifier("discord", "user_id", "100000000000000100") in identifiers
    # The snowflake ALONE — a renameable handle is never a matchable
    # identity: only the phone number / user id is stable. The /users/@me
    # response DOES carry a username (the fixture returns one), so this asserts
    # it is dropped, not merely absent.
    assert {i.kind for i in identifiers} == {"user_id"}
    # Value-level, not just kind-level: a mistake that added the username as
    # a SECOND "user_id"-kind identifier would pass the kind check above
    # while still smuggling it into self_aliases(). Pin the value set too.
    assert {i.value for i in identifiers} == {"100000000000000100"}


async def test_derive_identity_also_returns_the_rendered_handle(me_server):
    """The handle `<presence>` wants, read off
    the SAME response `derive_identity` already fetches — never a second
    call. `nora/domain/identity.py`'s no-username rule (above) covers the
    identifiers; this covers the display-text side of the same response."""
    _primary, _identifiers, handle = await derive_identity(
        "tok", base_url=str(me_server.make_url("")).rstrip("/")
    )

    assert handle == "@nora#1234"


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


# ---- render_handle: the live-vs-declared precedence's formatting half ------
#
# `discriminator = "0"` is what Discord reports for every account migrated to
# unique usernames; `derive_identity_also_returns_the_rendered_handle`
# above pins the pre-migration, numbered-tag shape (`discriminator = "1234"`, a
# stand-in for the four-digit tag measured live; 0001-9999).


def test_render_handle_with_a_real_discriminator_keeps_the_tag():
    assert render_handle("nora", "1234") == "@nora#1234"


def test_render_handle_with_discriminator_zero_drops_the_tag():
    """The migrated-account shape — Discord reports `"0"`, never absent, for
    a unique username."""
    assert render_handle("nora", "0") == "@nora"


def test_render_handle_with_no_discriminator_field_drops_the_tag():
    assert render_handle("nora", None) == "@nora"


def test_render_handle_with_no_username_is_none():
    assert render_handle(None, "1234") is None
    assert render_handle("", "1234") is None
    assert render_handle("   ", "0") is None
