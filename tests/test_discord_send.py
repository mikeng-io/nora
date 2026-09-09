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
    assert rest_server.calls[0] == {
        "content": "hello",
        "allowed_mentions": {"parse": [], "users": []},
    }


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


async def test_an_attachment_is_sent_as_multipart(aiohttp_server):
    """`attachments` is the payload and IS supported (2026-08-26): a
    multipart form with `payload_json` + `files[0]`, so a generated image
    arrives as a real Discord attachment."""
    from nora.domain.ports import OutboundAttachment

    calls: list[tuple[str, bytes]] = []

    async def handler(request: web.Request) -> web.Response:
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            data = await part.read()
            calls.append((str(part.name or ""), bytes(data)))
        return web.json_response({"id": "999999999999999999"}, status=200)

    app = web.Application()
    app.router.add_post("/channels/2/messages", handler)
    server = await aiohttp_server(app)

    sender = DiscordSender("tok", base_url=str(server.make_url("")).rstrip("/"))
    result = await sender.send(
        TARGET,
        "here you go",
        attachments=(
            OutboundAttachment(
                content=b"GIF89a", media_type="image/gif", filename="a.gif",
            ),
        ),
    )

    assert result.ok is True
    names = [name for name, _ in calls]
    assert "payload_json" in names
    assert "files[0]" in names
    assert any(b"GIF89a" in data for _, data in calls)


@pytest.fixture
async def rest_server_405(aiohttp_server):
    """Discord's OWN answer to a malformed channel route, and the reason this
    fixture is not `rest_server`: a server that simply has no matching route
    returns 404 and records nothing, so a test written against it passes
    whether or not the sender guards the request. It has to answer the way
    the real API does — **405 Method Not Allowed** on `/channels//messages`,
    which is what the deployment actually received — and it has to record the
    attempt, because "no request was made" is the whole assertion.
    """
    attempts: list[str] = []

    async def catch_all(request: web.Request) -> web.Response:
        attempts.append(request.path)
        return web.json_response({"message": "405: Method Not Allowed"}, status=405)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", catch_all)
    server = await aiohttp_server(app)
    server.attempts = attempts
    return server


async def test_send_refuses_a_conversation_id_that_names_no_channel(rest_server_405):
    """A bare channel snowflake — no guild half — is REFUSED before the wire.

    `partition("/")` puts the whole id in `guild_id` and leaves `channel_id`
    empty, so the POST would go to `/channels//messages`: a route that does
    not exist, which Discord answers **405**. That is what happened on the
    deployment (NORA-4) — `config.toml` held `shadow_channel = "<channel id>"`
    with no guild, and the Relevance Gate's review posts 405'd for a fortnight
    while the log upstream called it a guard refusal.

    Refusing here is worth more than the 405: the status says «this route
    rejects POST», the log line says which value is wrong. The assertion is
    that NO request is made — with the guard removed this same fixture records
    the `/channels//messages` attempt and the test goes red.
    """
    bare = ConversationTarget("discord", "2", "group")
    sender = DiscordSender("tok", base_url=str(rest_server_405.make_url("")).rstrip("/"))

    result = await sender.send(bare, "hello")

    assert result.ok is False
    assert rest_server_405.attempts == [], "a malformed target must not reach the wire"


async def test_a_well_formed_target_still_reaches_the_wire(rest_server_405):
    """The guard's other side: it must refuse the missing channel and nothing
    else. Without this, `return SendResult(ok=False)` at the top of `send`
    satisfies the test above."""
    sender = DiscordSender("tok", base_url=str(rest_server_405.make_url("")).rstrip("/"))

    await sender.send(TARGET, "hello")

    assert rest_server_405.attempts == ["/channels/2/messages"]
