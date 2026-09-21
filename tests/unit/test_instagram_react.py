"""`nora.platform.instagram.react.InstagramReactor` against the bridge's
`POST /react` (design/instagram-adoption/03-transport-contract.md, and the
route as `deploy/instagram-bridge/server.go` implements it).

The stand-in bridge RECORDS THE BODY and reproduces the route's own
wrong-call shape: `handleReact` 400s an empty emoji *even on a removal*,
because Instagram's mutation names WHICH reaction is being retracted. A fake
that accepted anything would let a retraction regress to a 400 on every call,
green.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget
from nora.platform.instagram.react import InstagramReactor

ROOM = "1009543911792316"
GROUP = ConversationTarget("instagram", ROOM, "group")
DM = ConversationTarget("instagram", ROOM, "dm")
MID = "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq"


@pytest.fixture
async def bridge():
    """`(box, reactor)`. The handler mirrors the real one's required fields."""
    box: dict = {"status": 200, "calls": []}

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        box["calls"].append(body)
        # The route's OWN validation, reproduced: message_id and emoji are
        # both required, the emoji also when `remove` is true.
        if not body.get("message_id") or not body.get("emoji"):
            return web.json_response({"error": "emoji is required"}, status=400)
        if box["status"] != 200:
            return web.json_response({"error": "boom"}, status=box["status"])
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/react", handler)
    server = TestServer(app)
    await server.start_server()
    yield box, InstagramReactor(str(server.make_url("")).rstrip("/"))
    await server.close()


async def test_a_reaction_posts_the_short_room_id_and_the_message_id(bridge):
    """SHORT, and it is load-bearing here in a way it is not for a text send:
    a reaction addressed with the LONG thread id is accepted with a 200 and is
    simply absent from the thread afterwards (measured 2026-09-22)."""
    box, reactor = bridge

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID)

    assert result.ok is True
    assert result.supported is True
    assert box["calls"] == [
        {"room_id": ROOM, "message_id": MID, "emoji": "❤️", "remove": False},
    ]


async def test_a_REMOVAL_still_carries_the_emoji(bridge):
    """The mutation identifies WHICH reaction is being retracted. Dropping the
    emoji would be a 400 on every retraction — which is exactly what the
    stand-in returns, so this test bites rather than asserting a shape."""
    box, reactor = bridge

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID, remove=True)

    assert result.ok is True
    assert box["calls"][0] == {
        "room_id": ROOM, "message_id": MID, "emoji": "❤️", "remove": True,
    }


async def test_no_message_id_is_UNSUPPORTED_not_a_failure(bridge):
    """A reaction pointed at nothing is not a reaction. `indicate` counts
    "this platform cannot do this" separately from "it tried and failed", so
    a wiring gap never reads as a transient error."""
    box, reactor = bridge

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id="")

    assert result.ok is False
    assert result.supported is False
    assert box["calls"] == []


async def test_a_dm_is_REFUSED_WITHOUT_AN_HTTP_CALL(bridge):
    """Same deny-by-default as send.py — a reaction is a reply-shaped outbound
    even though it is not a text send."""
    box, reactor = bridge

    result = await reactor.react(target=DM, emoji="❤️", to_message_id=MID)

    assert result.ok is False
    assert result.supported is False
    assert box["calls"] == []


async def test_a_CHECKPOINT_409_is_a_failure_the_platform_still_SUPPORTS(bridge):
    box, reactor = bridge
    box["status"] = 409

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID)

    assert result.ok is False
    assert result.supported is True


async def test_any_other_refusal_is_reported_rather_than_raised(bridge):
    box, reactor = bridge
    box["status"] = 500

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID)

    assert result.ok is False
    assert result.supported is True


async def test_an_unreachable_bridge_never_raises_into_the_loop():
    """R-5: decoration failing is never the group's problem, and a reaction
    that raised would reach a pipeline with no business handling one."""
    reactor = InstagramReactor("http://127.0.0.1:1", timeout=0.2)

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID)

    assert result.ok is False
    assert result.supported is True
