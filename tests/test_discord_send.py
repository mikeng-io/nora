import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.platform.discord.send import DiscordSender
from nora.platform.discord.snowflake import timestamp_ms

TARGET = ConversationTarget("discord", "1/2", "group")


@pytest.fixture
async def rest_server(aiohttp_server):
    calls: list[dict] = []
    headers: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        headers.append(dict(request.headers))
        return web.json_response({"id": "999999999999999999"}, status=200)

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)
    server.calls = calls
    server.headers = headers
    return server


async def test_send_posts_content_to_the_channel_from_the_conversation_id(rest_server):
    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.ok is True
    assert rest_server.calls[0] == {"content": "hello", "allowed_mentions": {"parse": [], "users": []}}


async def test_send_authenticates_with_the_bot_token(rest_server):
    sender = DiscordSender("my-secret-token", base_url=str(rest_server.make_url("")).rstrip("/"))

    await sender.send(TARGET, "hello")

    assert rest_server.headers[0]["Authorization"] == "Bot my-secret-token"


async def test_send_returns_the_platform_sent_timestamp(rest_server):
    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.timestamp == timestamp_ms(999999999999999999)


async def test_send_refuses_a_non_group_conversation_type(rest_server):
    dm_target = ConversationTarget("discord", "1/2", "dm")
    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(dm_target, "hello")

    assert result.ok is False
    assert rest_server.calls == []


async def test_send_returns_a_non_retryable_failure_on_a_server_error(aiohttp_server):
    async def handler(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)
    sender = DiscordSender("tok", base_url=str(server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.ok is False
    assert result.retryable is False


async def test_send_maps_http_429_to_a_retryable_failure(aiohttp_server):
    async def handler(request):
        return web.json_response({"retry_after": 2.5}, status=429)

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)
    sender = DiscordSender("tok", base_url=str(server.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "hello")

    assert result.ok is False
    assert result.retryable is True
    assert result.retry_after == 2.5


async def test_send_never_raises_on_a_connection_failure():
    sender = DiscordSender("tok", base_url="http://127.0.0.1:1")

    result = await sender.send(TARGET, "hello")

    assert result.ok is False


# ---------------------------------------------------------------------------
# mention resolution — the model writes '@Name', Discord needs '<@id>'
# ---------------------------------------------------------------------------


@pytest.fixture
async def rest_server_with_members(aiohttp_server):
    calls: list[dict] = []
    member_calls: list[str] = []
    state: dict = {"members": [], "status": 200}

    async def send_handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        return web.json_response({"id": "999999999999999999"}, status=200)

    async def members_handler(request: web.Request) -> web.Response:
        member_calls.append(request.match_info["guild"])
        if state["status"] != 200:
            return web.json_response({"message": "no"}, status=state["status"])
        return web.json_response(state["members"])

    app = web.Application()
    app.router.add_post("/channels/2/messages", send_handler)
    app.router.add_get("/guilds/{guild}/members", members_handler)
    server = await aiohttp_server(app)
    server.calls = calls
    server.member_calls = member_calls
    server.state = state
    return server


async def test_a_known_name_is_spliced_into_a_real_mention_before_sending(rest_server_with_members):
    rest_server_with_members.state["members"] = [
        {"user": {"id": "111", "username": "mike123"}, "nick": "Miles Novak"},
    ]
    sender = DiscordSender("tok", base_url=str(rest_server_with_members.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "@Miles Novak 快撚啲")

    assert result.ok is True
    assert rest_server_with_members.calls[0] == {
        "content": "<@111> 快撚啲",
        "allowed_mentions": {"parse": [], "users": ["111"]},
    }


async def test_the_guild_half_of_the_conversation_id_is_what_is_queried(rest_server_with_members):
    """`target.conversation_id` is `<guild_id>/<channel_id>`; the member
    lookup needs the guild half, same as `DiscordRoster`."""
    sender = DiscordSender("tok", base_url=str(rest_server_with_members.make_url("")).rstrip("/"))

    await sender.send(TARGET, "@Miles Novak hi")

    assert rest_server_with_members.member_calls == ["1"]


async def test_text_with_no_at_sign_never_triggers_a_member_lookup(rest_server_with_members):
    """The common case — most replies mention nobody — must not pay for a
    guild member fetch on every single send."""
    sender = DiscordSender("tok", base_url=str(rest_server_with_members.make_url("")).rstrip("/"))

    await sender.send(TARGET, "no mentions here")

    assert rest_server_with_members.member_calls == []


async def test_an_unrecognized_name_is_sent_as_plain_text(rest_server_with_members):
    rest_server_with_members.state["members"] = [
        {"user": {"id": "111", "username": "mike123"}, "nick": "Miles Novak"},
    ]
    sender = DiscordSender("tok", base_url=str(rest_server_with_members.make_url("")).rstrip("/"))

    await sender.send(TARGET, "@Someone Else hi")

    assert rest_server_with_members.calls[0] == {
        "content": "@Someone Else hi",
        "allowed_mentions": {"parse": [], "users": []},
    }


async def test_a_failed_member_lookup_falls_back_to_the_original_text(rest_server_with_members):
    """Fail-closed: never guess, never raise mid-send — the same degraded
    behaviour as before this feature existed, an inert '@Name'."""
    rest_server_with_members.state["status"] = 403
    sender = DiscordSender("tok", base_url=str(rest_server_with_members.make_url("")).rstrip("/"))

    result = await sender.send(TARGET, "@Miles Novak hi")

    assert result.ok is True
    assert rest_server_with_members.calls[0] == {
        "content": "@Miles Novak hi",
        "allowed_mentions": {"parse": [], "users": []},
    }


async def test_an_attachment_is_refused_rather_than_silently_dropped(rest_server):
    """`attachments` is the payload, not decoration.

    The sender has no multipart upload yet. Sending the caption alone and
    answering `ok=True` would make `Deliverer` write the row SENT with its
    media metadata, tell the model `send_gif` succeeded, tick
    `nora_replies_total{status="sent"}` and raise the ✅ — while the channel
    saw a caption pointing at nothing. Every instrument would agree on a
    delivery that did not happen, which is the exact silent-failure shape R-5
    exists to forbid.

    Asserts `rest_server.calls == []` too: the refusal must happen BEFORE the
    network, so no half-message is posted.
    """
    from nora.domain.ports import OutboundAttachment

    sender = DiscordSender("tok", base_url=str(rest_server.make_url("")).rstrip("/"))

    result = await sender.send(
        # TARGET, whose channel the stand-in server actually routes — an
        # unrouted channel would 404 and make this pass without the guard.
        TARGET,
        "here you go",
        attachments=(
            OutboundAttachment(
                content=b"GIF89a", media_type="image/gif", filename="a.gif",
            ),
        ),
    )

    assert result.ok is False
    assert rest_server.calls == [], "nothing may be posted when the payload cannot be"
