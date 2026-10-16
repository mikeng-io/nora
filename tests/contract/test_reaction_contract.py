"""One suite, EVERY implementation of `PlatformAdapter.react`.

CLAUDE.md: "A port never gains a second implementation — including a test fake
— without a shared contract suite both pass." `react()` is on
`nora.domain.ports.PlatformAdapter`, so `nora.testing.FakeAdapter.react` is a
genuine second implementation and `DiscordAdapter.react` a third; a shared
vocabulary held by hand-copied convention alone would drift. `FakeAdapter`'s
documented exemption does not cover it: that exemption is about `.parse()` and
`.listen()`, neither of which is on the port, whereas `react()` is.

The property that actually matters here is the last one — a reaction NEVER
raises, whatever the platform does. Every caller of this port method
(`nora.engagement.stages.indicate`) runs detached under `spawn`, so an
exception has no one to report to and would surface only as a lost background
task. A fake that raised where production returns, or vice versa, would make
the pipeline tests prove the wrong thing.
"""

import inspect

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import PlatformAdapter, ReactionResult
from nora.obs.metrics import Metrics
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.whatsapp.adapter import WhatsAppAdapter
from nora.testing import FakeAdapter

DISCORD_GROUP = ConversationTarget("discord", "111/222", "group")
DISCORD_MESSAGE = "100000000000000208"

WHATSAPP_GROUP = ConversationTarget("whatsapp", "152099000000000000@g.us", "group")

#: The SHORT thread id. On the reaction route that is load-bearing rather than
#: merely matching upstream: a reaction addressed with the LONG one is accepted
#: with a 200 and is absent from the thread afterwards.
INSTAGRAM_GROUP = ConversationTarget("instagram", "1183780976338188", "group")
INSTAGRAM_MESSAGE = "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq"

GROUP = ConversationTarget("signal", "Z3JvdXA=", "group")
ACI = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"


async def _whatsapp_adapter(aiohttp_server, *, status: int = 200):
    """A `WhatsAppAdapter` pointed at a stand-in for the bridge's `/react`."""
    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.json_response({"error": "boom"}, status=status)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/react", handler)
    server = await aiohttp_server(app)
    return WhatsAppAdapter(str(server.make_url("")).rstrip("/"))


async def _instagram_adapter(aiohttp_server, *, status: int = 200):
    """An `InstagramAdapter` pointed at a stand-in for the bridge's
    `PUT /threads/{thread_fbid}/messages/{message_id}/reaction`,
    answering `{confirmed}` with no `ok`, and the error envelope on failure."""
    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.json_response(
                {"error": {"code": "internal", "message": "boom"}}, status=status,
            )
        return web.json_response({"confirmed": True})

    app = web.Application()
    app.router.add_put("/threads/{thread_fbid}/messages/{message_id}/reaction", handler)
    server = await aiohttp_server(app)
    return InstagramAdapter(str(server.make_url("")).rstrip("/"))


async def _discord_adapter(aiohttp_server, *, status: int = 204):
    """A `DiscordAdapter` pointed at a stand-in for the reactions endpoint."""
    async def handler(request: web.Request) -> web.Response:
        if status == 204:
            return web.Response(status=204)
        return web.json_response({"message": "boom"}, status=status)

    app = web.Application()
    app.router.add_route(
        "*", "/channels/{channel}/messages/{message}/reactions/{emoji}/@me", handler,
    )
    server = await aiohttp_server(app)
    return DiscordAdapter("tok", base_url=str(server.make_url("")).rstrip("/"))


async def _signal_adapter(aiohttp_server, *, status: int = 200):
    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if status != 200:
            return web.Response(status=status, text="boom")
        return web.json_response({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    # A placeholder account against a local aiohttp test server — nothing in
    # this suite can reach the real daemon or the real group.
    return SignalAdapter(str(server.make_url("")).rstrip("/"), "+15550000000", metrics=Metrics())


@pytest.fixture(params=["signal", "discord", "whatsapp", "instagram", "fake"])
async def working(request, aiohttp_server):
    """An adapter whose platform accepts the reaction."""
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
    """An adapter whose platform HAS reactions but rejects this one."""
    if request.param == "signal":
        return await _signal_adapter(aiohttp_server, status=500)
    if request.param == "discord":
        # 403 — the bot lacks Add Reactions in that channel. The platform CAN
        # react; this attempt was refused, which is the distinction this suite
        # exists to keep identical across implementations.
        return await _discord_adapter(aiohttp_server, status=403)
    if request.param == "whatsapp":
        # 500 — the bridge itself failed the call. Same distinction: the
        # platform CAN react; this attempt was refused.
        return await _whatsapp_adapter(aiohttp_server, status=500)
    if request.param == "instagram":
        # 500 — the bridge's own fail-closed default for a refusal it cannot
        # classify. Same distinction: the platform CAN react; this attempt was
        # refused.
        return await _instagram_adapter(aiohttp_server, status=500)
    return FakeAdapter(account="+15550000000", react_fails=True)


def _target_for(adapter):
    """Each implementation's own room, since a Discord adapter cannot address
    a Signal group id and vice versa."""
    platform = getattr(adapter, "platform", "")
    if platform == "discord":
        return DISCORD_GROUP
    if platform == "whatsapp":
        return WHATSAPP_GROUP
    if platform == "instagram":
        return INSTAGRAM_GROUP
    return GROUP


@pytest.mark.parametrize(
    "impl", [SignalAdapter, DiscordAdapter, WhatsAppAdapter, InstagramAdapter, FakeAdapter],
)
def test_react_matches_the_port_signature(impl):
    """Keyword-only after self, per `tests/test_ports.py`'s convention, and
    `ReactionResult`-returning. `isinstance` against a runtime_checkable
    Protocol only checks names, so the shape has to be asserted directly."""
    params = inspect.signature(impl.react).parameters
    # `to_message_id` included (a snowflake is the only thing Discord's endpoint
    # accepts), and this list not knowing about it is exactly how an
    # implementation drifts.
    for name in ("target", "emoji", "to_author", "to_timestamp", "to_message_id", "remove"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert inspect.signature(impl.react).return_annotation == "ReactionResult"


@pytest.mark.parametrize(
    "impl", [SignalAdapter, DiscordAdapter, WhatsAppAdapter, InstagramAdapter, FakeAdapter],
)
def test_every_implementation_is_a_platform_adapter(impl):
    if impl is SignalAdapter:
        instance = impl("http://x", "+1", metrics=Metrics())
    elif impl is DiscordAdapter:
        instance = impl("tok")
    elif impl in (WhatsAppAdapter, InstagramAdapter):
        instance = impl("http://x")
    else:
        instance = impl()
    assert isinstance(instance, PlatformAdapter)


async def test_a_successful_reaction_is_ok_and_supported(working):
    result = await working.react(
        target=_target_for(working), emoji="\N{EYES}", to_author=ACI,
        to_timestamp=1754300000000, to_message_id=DISCORD_MESSAGE,
    )
    assert result == ReactionResult(ok=True, supported=True)


async def test_removing_a_reaction_is_accepted(working):
    result = await working.react(
        target=_target_for(working), emoji="\N{EYES}", to_author=ACI,
        to_timestamp=1754300000000, to_message_id=DISCORD_MESSAGE, remove=True,
    )
    assert result.ok is True


async def test_a_rejected_reaction_reports_failure_not_unsupported(broken):
    """A platform that HAS reactions and refused one is `ok=False,
    supported=True`. Reporting it as unsupported would hide a wedged daemon
    behind a label that means "working as designed"."""
    result = await broken.react(
        target=_target_for(broken), emoji="\N{EYES}", to_author=ACI,
        to_timestamp=1754300000000, to_message_id=DISCORD_MESSAGE,
    )
    assert result == ReactionResult(ok=False, supported=True)


async def test_a_reaction_never_raises(broken):
    """The load-bearing property. PROBE: make either implementation raise on
    failure instead of returning, and this is the assertion that fails."""
    try:
        result = await broken.react(
            target=_target_for(broken), emoji="\N{EYES}", to_author=ACI,
            to_timestamp=1754300000000, to_message_id=DISCORD_MESSAGE,
        )
    except Exception as exc:  # noqa: BLE001 — catching broadly IS the assertion here
        pytest.fail(f"react() raised {type(exc).__name__}; it must always return")
    assert isinstance(result, ReactionResult)
