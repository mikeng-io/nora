"""`nora.platform.whatsapp.react.WhatsAppReactor` — reactions through the
bridge's `POST /react`, mirroring `tests/test_discord_react.py`'s "distinguish
the failure modes" fixture discipline."""

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import ReactionResult
from nora.platform.whatsapp.react import WhatsAppReactor

TARGET = ConversationTarget("whatsapp", "152099000000000000@g.us", "group")
MESSAGE_ID = "3EB0F1A2B3C4"
EYES = "\U0001f440"


@pytest.fixture
async def bridge(aiohttp_server):
    state: dict = {"calls": [], "status": 200}

    async def handler(request: web.Request) -> web.Response:
        state["calls"].append(await request.json())
        if state["status"] == 404:
            return web.json_response({"error": "unknown_message"}, status=404)
        if state["status"] != 200:
            return web.json_response({"error": "boom"}, status=state["status"])
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/react", handler)
    server = await aiohttp_server(app)
    server.state = state
    return server


def _reactor(server) -> WhatsAppReactor:
    return WhatsAppReactor(str(server.make_url("")).rstrip("/"))


async def test_a_placed_reaction_posts_the_contract_shape(bridge):
    result = await _reactor(bridge).react(
        target=TARGET, emoji=EYES, to_message_id=MESSAGE_ID,
    )
    assert result == ReactionResult(ok=True, supported=True)
    assert bridge.state["calls"] == [{
        "jid": TARGET.conversation_id,
        "message_id": MESSAGE_ID,
        "emoji": EYES,
        "remove": False,
    }]


async def test_a_removal_carries_remove_true(bridge):
    result = await _reactor(bridge).react(
        target=TARGET, emoji=EYES, to_message_id=MESSAGE_ID, remove=True,
    )
    assert result.ok is True
    assert bridge.state["calls"][0]["remove"] is True


async def test_an_empty_message_id_is_unsupported_not_a_guess(bridge):
    """`supported=False`, mirroring `DiscordReactor`: without the platform's
    own handle there is nothing to react to, and guessing at a message is
    worse than saying so. `indicate` counts the two statuses differently."""
    result = await _reactor(bridge).react(target=TARGET, emoji=EYES, to_message_id="")
    assert result == ReactionResult(ok=False, supported=False)
    assert bridge.state["calls"] == []


async def test_a_404_unknown_message_is_a_cache_miss_not_unsupported(bridge, caplog):
    """The stanza id is not in the bridge's bounded recent-message cache —
    the platform HAS reactions; this one could not be addressed."""
    bridge.state["status"] = 404
    result = await _reactor(bridge).react(
        target=TARGET, emoji=EYES, to_message_id=MESSAGE_ID,
    )
    assert result == ReactionResult(ok=False, supported=True)


async def test_another_non_200_is_ok_false_and_supported(bridge):
    bridge.state["status"] = 500
    result = await _reactor(bridge).react(
        target=TARGET, emoji=EYES, to_message_id=MESSAGE_ID,
    )
    assert result == ReactionResult(ok=False, supported=True)


async def test_an_unreachable_bridge_never_raises():
    reactor = WhatsAppReactor("http://127.0.0.1:1", timeout=0.5)
    result = await reactor.react(target=TARGET, emoji=EYES, to_message_id=MESSAGE_ID)
    assert result == ReactionResult(ok=False, supported=True)
