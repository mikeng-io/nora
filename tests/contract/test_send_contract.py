"""One suite, every implementation of `PlatformAdapter.send`.

CLAUDE.md: "A port never gains a second implementation — including a test fake
— without a shared contract suite both pass." `send()` takes `mentions`,
`styles` and `preview`, and `nora.testing.FakeAdapter.send` is what every
pipeline, integration and eval test asserts against — so without this suite a
fake that quietly dropped the decoration would let all of them prove a mention
or a bold range was COMPOSED while proving nothing about whether it reached a
transport. `FakeAdapter`'s documented exemption covers `.parse()`/`.listen()`,
which are not on the port; `send()` is.

The malformed-range cases at the bottom are where a fake most easily stops
being a model of the server. signal-cli fails the WHOLE send on a range it
cannot parse, so «the message still goes out» is a property both
implementations owe — and a fake that recorded the bad range anyway would let a
test assert something the daemon would have refused.

The half that only the real adapter can be asked about — the JSON-RPC parameter
NAMES — lives in `tests/test_signal_send.py`, against a server that inspects
the body. What belongs HERE is what both must agree on: that the arguments are
accepted at all, that they are keyword-safe, and that neither implementation
turns a decorated send into a failure.
"""

import inspect

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import (
    LinkPreview,
    Mention,
    PlatformAdapter,
    SendResult,
    Style,
    TextStyle,
)
from nora.obs.metrics import Metrics
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.whatsapp.adapter import WhatsAppAdapter
from nora.testing import FakeAdapter

GROUP = ConversationTarget("signal", "Z3JvdXA=", "group")
DISCORD_GROUP = ConversationTarget("discord", "111/222", "group")
WHATSAPP_GROUP = ConversationTarget("whatsapp", "152099000000000000@g.us", "group")
#: The SHORT thread id — the only one `03` exposes, and the only one every
#: send verb takes.
INSTAGRAM_GROUP = ConversationTarget("instagram", "1183780976338188", "group")


def _target_for(adapter):
    """Each implementation's own room — a Discord adapter cannot address a
    Signal group id, and vice versa."""
    platform = getattr(adapter, "platform", "")
    if platform == "discord":
        return DISCORD_GROUP
    if platform == "whatsapp":
        return WHATSAPP_GROUP
    if platform == "instagram":
        return INSTAGRAM_GROUP
    return GROUP
ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"
MENTION = Mention(start=0, length=8, recipient=ACI)
STYLE = TextStyle(start=0, length=5, style=Style.BOLD)
PREVIEW = LinkPreview(url="https://links.example.net/a", title="A page", description="about it")


async def _signal_adapter(aiohttp_server, *, status: int = 200):
    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if status != 200:
            return web.Response(status=status, text="boom")
        return web.json_response({
            "jsonrpc": "2.0", "id": body.get("id"), "result": {"timestamp": 1754300000000},
        })

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    # A placeholder account against a local aiohttp test server — nothing in
    # this suite can reach the real daemon or the real group.
    return SignalAdapter(str(server.make_url("")).rstrip("/"), "+15550000000", metrics=Metrics())


async def _discord_adapter(aiohttp_server, *, status: int = 200):
    """A `DiscordAdapter` pointed at a stand-in for the messages endpoint."""
    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.json_response({"message": "boom"}, status=status)
        return web.json_response({"id": "100000000000000208"})

    app = web.Application()
    app.router.add_post("/channels/{channel}/messages", handler)
    server = await aiohttp_server(app)
    return DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))


async def _whatsapp_adapter(aiohttp_server, *, status: int = 200):
    """A `WhatsAppAdapter` pointed at a stand-in for the bridge's `/send`."""
    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.json_response({"error": "boom"}, status=status)
        return web.json_response({"ok": True, "id": "3EB0F1", "timestamp_ms": 1754300000000})

    app = web.Application()
    app.router.add_post("/send", handler)
    server = await aiohttp_server(app)
    return WhatsAppAdapter(str(server.make_url("")).rstrip("/"))


async def _instagram_adapter(aiohttp_server, *, status: int = 200):
    """An `InstagramAdapter` pointed at a stand-in for the bridge's
    `POST /threads/{thread_fbid}/messages`, answering its shapes:
    `{id, timestamp_ms}` with no `ok`, and the error envelope on failure."""
    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.json_response(
                {"error": {"code": "internal", "message": "boom"}}, status=status,
            )
        return web.json_response({"id": "mid.$x", "timestamp_ms": 1754300000000})

    app = web.Application()
    app.router.add_post("/threads/{thread_fbid}/messages", handler)
    server = await aiohttp_server(app)
    return InstagramAdapter(str(server.make_url("")).rstrip("/"))


@pytest.fixture(params=["signal", "discord", "whatsapp", "instagram", "fake"])
async def working(request, aiohttp_server):
    if request.param == "signal":
        return await _signal_adapter(aiohttp_server)
    if request.param == "discord":
        return await _discord_adapter(aiohttp_server)
    if request.param == "whatsapp":
        return await _whatsapp_adapter(aiohttp_server)
    if request.param == "instagram":
        return await _instagram_adapter(aiohttp_server)
    return FakeAdapter(account="+15550000000")


@pytest.fixture(params=["signal", "discord", "whatsapp", "instagram", "fake"])
async def broken(request, aiohttp_server):
    """An adapter whose transport refuses the send."""
    if request.param == "signal":
        return await _signal_adapter(aiohttp_server, status=500)
    if request.param == "discord":
        return await _discord_adapter(aiohttp_server, status=500)
    if request.param == "whatsapp":
        return await _whatsapp_adapter(aiohttp_server, status=500)
    if request.param == "instagram":
        return await _instagram_adapter(aiohttp_server, status=500)
    return FakeAdapter(account="+15550000000", succeed=False)


@pytest.mark.parametrize(
    "impl", [SignalAdapter, DiscordAdapter, WhatsAppAdapter, InstagramAdapter, FakeAdapter],
)
def test_send_accepts_the_ports_decoration_parameters(impl):
    """`isinstance` against a runtime_checkable Protocol only checks NAMES, so
    a fake whose `send` kept an older two-parameter shape would still be a
    `PlatformAdapter` by that test and would `TypeError` the moment `Deliverer`
    called it. The shape has to be asserted directly."""
    params = inspect.signature(impl.send).parameters
    for name in ("target", "text", "mentions", "styles", "preview"):
        assert name in params, name
    assert params["mentions"].default == ()
    assert params["styles"].default == ()
    assert params["preview"].default is None


@pytest.mark.parametrize(
    "impl", [SignalAdapter, DiscordAdapter, WhatsAppAdapter, InstagramAdapter, FakeAdapter],
)
def test_both_implementations_are_platform_adapters(impl):
    if impl is SignalAdapter:
        instance = impl("http://x", "+1", metrics=Metrics())
    elif impl is DiscordAdapter:
        instance = impl("tok")
    elif impl in (WhatsAppAdapter, InstagramAdapter):
        instance = impl("http://x")
    else:
        instance = impl()
    assert isinstance(instance, PlatformAdapter)


async def test_a_plain_send_still_succeeds(working):
    result = await working.send(target=_target_for(working), text="hello")
    assert result.ok is True


async def test_a_send_carrying_a_mention_succeeds(working):
    result = await working.send(
        target=_target_for(working), text="@Grant hi", mentions=(MENTION,),
    )
    assert result.ok is True


async def test_a_send_carrying_a_style_succeeds(working):
    result = await working.send(target=_target_for(working), text="hello there", styles=(STYLE,))
    assert result.ok is True


async def test_a_send_carrying_a_preview_succeeds(working):
    result = await working.send(
        target=_target_for(working), text="https://links.example.net/a", preview=PREVIEW,
    )
    assert result.ok is True


async def test_a_send_carrying_every_decoration_at_once_succeeds(working):
    result = await working.send(
        target=_target_for(working), text="@Grant https://links.example.net/a",
        mentions=(MENTION,), styles=(STYLE,), preview=PREVIEW,
    )
    assert result.ok is True


async def test_a_refused_send_reports_failure_rather_than_raising(broken):
    """The load-bearing property, and the one decoration must not change:
    `Deliverer` catches an exception here anyway, but a `SendResult(ok=False)`
    is what lets it record the FAILED delivery status and count
    `send_failed` — an exception loses the outcome distinction."""
    try:
        result = await broken.send(target=_target_for(broken), text="hi", mentions=(MENTION,))
    except Exception as exc:  # noqa: BLE001 — catching broadly IS the assertion here
        pytest.fail(f"send() raised {type(exc).__name__}; it must always return")
    assert isinstance(result, SendResult)
    assert result.ok is False


async def test_a_malformed_mention_range_never_costs_the_message(working):
    """signal-cli throws `UserErrorException` on a range its `(\\d+):(\\d+):(.+)`
    regex rejects, and that fails the WHOLE send — so a bad range does not
    degrade to a missing chip, it degrades to silence (R-5's shape). Both
    implementations must send the message anyway."""
    result = await working.send(
        target=_target_for(working), text="hello",
        mentions=(Mention(start=-1, length=0, recipient=""),),
    )
    assert result.ok is True


async def test_a_style_range_that_does_not_fit_never_costs_the_message(working):
    """Same rule, checked against the live daemon: it
    answered `-1:3:BOLD` with «Invalid textStyle syntax» and delivered nothing.

    `12:9` is the case a syntax check alone would miss — it matches the regex
    and indexes text that does not exist — which is why both implementations
    bound the range against the message rather than merely parsing it.
    """
    result = await working.send(
        target=_target_for(working), text="hello",
        styles=(
            TextStyle(start=-1, length=3, style=Style.BOLD),
            TextStyle(start=12, length=9, style=Style.BOLD),
        ),
    )
    assert result.ok is True
