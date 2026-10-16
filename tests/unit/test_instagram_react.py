"""`nora.platform.instagram.react.InstagramReactor` against the bridge's
`PUT /threads/{thread_fbid}/messages/{message_id}/reaction`
(as `deploy/instagram-bridge/internal/transport/http/messages.go` implements it).

The stand-in bridge RECORDS THE PATH AND THE BODY and reproduces the route's
own wrong-call shape: `handleReaction` 400s an empty emoji *even on a removal*,
because Instagram's mutation names WHICH reaction is being retracted. A fake
that accepted anything would let a retraction regress to a 400 on every call,
green. It serves ONLY the new route, so a reactor still on `POST /react` gets
aiohttp's 404 and the success tests go red; its answers are the bridge's own
bodies from `tests/fixtures/instagram_bridge/reaction_*`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget
from nora.platform.instagram.react import InstagramReactor

ROOM = "1183780976338188"
GROUP = ConversationTarget("instagram", ROOM, "group")
DM = ConversationTarget("instagram", ROOM, "dm")
MID = "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq"


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "instagram_bridge"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture
async def bridge():
    """`(box, reactor)`. The handler mirrors the real one's required fields.
    `box["calls"]` is `[(room, message_id, body), …]`."""
    box: dict = {"status": 200, "calls": []}

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        box["calls"].append(
            (request.match_info["thread_fbid"], request.match_info["message_id"], body),
        )
        # The route's OWN validation, reproduced: the emoji is required, also
        # when `remove` is true. The refusal is the bridge's own envelope.
        if not body.get("emoji"):
            fx = _fixture("reaction_no_emoji_400.json")
            return web.json_response(fx["body"], status=fx["status"])
        if box["status"] != 200:
            fx = _fixture("reaction_checkpointed_409.json")
            return web.json_response(fx["body"], status=box["status"])
        return web.json_response(_fixture("reaction_ok.json")["body"])

    app = web.Application()
    app.router.add_put("/threads/{thread_fbid}/messages/{message_id}/reaction", handler)
    server = TestServer(app)
    await server.start_server()
    yield box, InstagramReactor(str(server.make_url("")).rstrip("/"))
    await server.close()


async def test_a_reaction_posts_the_short_room_id_and_the_message_id(bridge):
    """SHORT, and it is load-bearing here in a way it is not for a text send:
    a reaction addressed with the LONG thread id is accepted with a 200 and is
    simply absent from the thread afterwards."""
    box, reactor = bridge

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID)

    assert result.ok is True
    assert result.supported is True
    assert box["calls"] == [(ROOM, MID, {"emoji": "❤️", "remove": False})]


async def test_a_REMOVAL_still_carries_the_emoji(bridge):
    """The mutation identifies WHICH reaction is being retracted. Dropping the
    emoji would be a 400 on every retraction — which is exactly what the
    stand-in returns, so this test bites rather than asserting a shape."""
    box, reactor = bridge

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=MID, remove=True)

    assert result.ok is True
    assert box["calls"][0] == (ROOM, MID, {"emoji": "❤️", "remove": True})


async def test_an_UNCONFIRMED_reaction_is_still_a_success():
    """`{"confirmed": false}`: accepted, though the mutation's own post-state
    was silent, as it is on a reaction that lands. The status is 200, so it is a
    success; only the status is read."""

    async def unconfirmed(request: web.Request) -> web.Response:
        fx = _fixture("reaction_unconfirmed.json")
        return web.json_response(fx["body"], status=fx["status"])

    app = web.Application()
    app.router.add_put("/threads/{thread_fbid}/messages/{message_id}/reaction", unconfirmed)
    server = TestServer(app)
    await server.start_server()
    try:
        result = await InstagramReactor(str(server.make_url("")).rstrip("/")).react(
            target=GROUP, emoji="❤️", to_message_id=MID,
        )
    finally:
        await server.close()
    assert result.ok is True


async def test_a_message_id_is_ONE_path_segment(bridge):
    """A `mid.$…` carries `$` and nothing promises it never carries `/`; it is
    quoted into its own segment and arrives byte for byte."""
    box, reactor = bridge
    odd = "mid.$gA+b/c=d"

    result = await reactor.react(target=GROUP, emoji="❤️", to_message_id=odd)

    assert result.ok is True
    assert box["calls"][0][1] == odd


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
