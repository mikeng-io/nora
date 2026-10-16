"""`InstagramRoster` — group membership for Instagram rooms.

The shared behaviour lives in `tests/contract/test_roster_source_contract.py`,
which this implementation passes alongside Signal's, Discord's and the
fake. What is here is what only Instagram has: the two id spaces, and the
bridge's own refusal shapes.

The property every test below is a facet of: `available=False` and an EMPTY
roster must never be the same value. `Roster.available` exists because an
unreachable source and a room with nobody in it would otherwise be
indistinguishable, and every room-scoped tool would answer «there is nobody
here» when the truth is «I could not look». On Instagram that second sentence
is routine — an ordinary captcha checkpoints the session — so the distinction
is not a corner case here.
"""

from __future__ import annotations

import logging

import pytest
from aiohttp import web

from nora.platform.instagram.roster import InstagramRoster

ROOM = "1183780976338188"

# A three-person room, as the bridge serves it: TWO rows, because
# `Users[]` excludes the viewer and Nora is not a member of her own room. The
# fbids are 15 and 17 digits, the second sharing Nora's own `1784…` prefix —
# which is exactly why no length or prefix rule may ever be used to tell an
# fbid from an igid.
MEMBERS = [
    {"fbid": "186217604552756", "igid": "61652861337",
     "username": "nka_bpumvg", "full_name": "Miles"},
    {"fbid": "18457536310970624", "igid": "72038255145",
     "username": "third_member", "full_name": ""},
]


@pytest.fixture
async def bridge(aiohttp_server):
    """A stand-in for the bridge's `GET /threads/{thread_fbid}/members` that distinguishes its own
    failure modes instead of answering everything identically."""
    state: dict = {"status": 200, "body": None, "text": None, "rooms": []}

    async def handler(request: web.Request) -> web.Response:
        state["rooms"].append(request.match_info.get("thread_fbid"))
        if state["text"] is not None:
            return web.Response(text=state["text"], content_type="application/json")
        if state["status"] != 200:
            return web.json_response(
                {"error": {"code": "internal", "message": "no"}}, status=state["status"],
            )
        body = state["body"]
        if body is None:
            body = {"room_id": ROOM, "title": "the group",
                    "is_group": True, "members": MEMBERS}
        return web.json_response(body)

    app = web.Application()
    app.router.add_get("/threads/{thread_fbid}/members", handler)
    server = await aiohttp_server(app)
    server.state = state
    return server


def _roster(server) -> InstagramRoster:
    return InstagramRoster(str(server.make_url("")).rstrip("/"))


async def test_one_frozenset_per_member_holding_the_MESSAGING_fbid(bridge):
    """One id form per account, and it is `interop_messaging_user_fbid`.

    The igid the bridge also sends is deliberately absent, and this is the
    assertion that keeps it absent. `_preferred_recipient`'s Instagram branch
    accepts any bare digit string, which is what BOTH id kinds are — so an igid
    in this frozenset is a coin toss on addressing an @-mention with a number
    no mention can carry. That is the silent no-ping failure this roster exists
    to end, and admitting the public id here would re-introduce it one layer
    down, where nothing would report it either.
    """
    roster = await _roster(bridge).roster(conversation_id=ROOM)

    assert roster.available is True
    assert set(roster.members) == {
        frozenset({"186217604552756"}),
        frozenset({"18457536310970624"}),
    }
    assert "61652861337" not in set().union(*roster.members), (
        "the public igid must never be a membership token"
    )


async def test_the_short_thread_id_is_what_is_asked_for(bridge):
    """The room key is the SHORT thread id, verbatim — the only thread id the
    transport contract exposes, and the one `policy.toml` matches on."""
    await _roster(bridge).roster(conversation_id=ROOM)

    assert bridge.state["rooms"] == [ROOM]


async def test_a_checkpointed_session_is_unavailable_and_names_itself(bridge, caplog):
    """409 — the session is checkpointed or logged out.

    The most likely Instagram failure by far: a captcha is a NORMAL operating
    state on this platform, not a crash. An empty roster here would be
    catastrophic in a specific way — `where_is`'s disclosure gate would be
    evaluating a room it believes holds nobody.

    The RETURN VALUE is not all this pins: 409 falls through to the same
    `available=False` a 500 gives, so asserting only that would pass with the
    whole branch deleted. What the branch buys is the diagnostic, which is what
    an operator greps for when every room-scoped tool on Instagram goes quiet
    at once.
    """
    bridge.state["status"] = 409

    with caplog.at_level(logging.ERROR, logger="nora.instagram.roster"):
        roster = await _roster(bridge).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()
    assert any(
        "checkpointed or logged out" in r.message for r in caplog.records
    ), "a 409 must name the session state — it is the one failure a human fixes"


@pytest.mark.parametrize("status", [400, 500, 503])
async def test_an_http_error_is_unavailable_not_an_empty_room(bridge, status):
    bridge.state["status"] = status

    roster = await _roster(bridge).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()


async def test_an_unreachable_bridge_never_raises():
    """`RosterSource.roster` is documented never to raise: the caller is a tool
    inside a live turn, and an exception there has nobody to report to — it
    would either kill the tag or be swallowed into «nobody is in this group»."""
    roster = await InstagramRoster(
        "http://127.0.0.1:1", timeout_seconds=0.2,
    ).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()


async def test_malformed_json_is_unavailable_and_never_raises(bridge):
    """A 200 whose body is not JSON at all. `resp.json()` raises for this, and
    an exception escaping here is exactly what the port forbids."""
    bridge.state["text"] = '{"room_id": "1", "members": [{'

    roster = await _roster(bridge).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()


@pytest.mark.parametrize("body", [
    pytest.param([], id="a list where an object was expected"),
    pytest.param({"room_id": "1"}, id="no members key at all"),
    pytest.param({"room_id": "1", "members": {}}, id="members is not a list"),
    pytest.param({"room_id": "1", "members": []}, id="members is empty"),
])
async def test_a_body_that_cannot_be_read_as_a_room_is_unavailable(bridge, body):
    """Including the EMPTY list, which is the one that looks like an answer.

    A group Nora is in holds at least one account other than her own, so an
    empty member list is the read having nothing to say — not the room having
    emptied. Served as `available=True, members=()` it would be acted on as
    «there is nobody here».
    """
    bridge.state["body"] = body

    roster = await _roster(bridge).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()


async def test_a_member_with_no_fbid_refuses_the_WHOLE_roster(bridge, caplog):
    """Under-reporting is the one direction that is unsafe.

    `Whereis` refuses a room holding an account it cannot NAME (`unnamed > 0`),
    so an account it cannot SEE is one the disclosure gate never gets to refuse
    over — a roster short by one member is a roster the gate happily approves.
    Skipping the row is the tempting choice and it is the wrong one; the whole
    read is refused instead, which costs an honest «I couldn't look».
    """
    bridge.state["body"] = {
        "members": [MEMBERS[0], {"igid": "72038255145", "username": "third_member"}],
    }

    with caplog.at_level(logging.ERROR, logger="nora.instagram.roster"):
        roster = await _roster(bridge).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()
    assert any("under-report the room" in r.message for r in caplog.records)


@pytest.mark.parametrize("bad", ["", "   "])
async def test_an_empty_conversation_id_never_reaches_the_bridge(bridge, bad):
    """A wiring fault, not an empty room — and it must not be attempted, where
    it would come back as an indistinguishable 400."""
    roster = await _roster(bridge).roster(conversation_id=bad)

    assert roster.available is False
    assert bridge.state["rooms"] == []


async def test_the_platform_it_is_bound_to_is_declared():
    """`roster()` takes no `platform` — an implementation is bound to one,
    exactly as `PlatformAdapter` is, and `composition` keys the map on it."""
    assert InstagramRoster("http://x").platform == "instagram"


async def test_a_conversation_id_that_is_not_a_string_never_raises():
    """The port's rule is absolute: `roster()` must never raise, because the
    caller is a tool inside a live turn and an exception there is a broken
    turn rather than a degraded answer.

    A non-str conversation_id must not leave through an AttributeError from
    `.strip()`. Both call sites wrap it today — that is their belt, not
    this module's braces, and a third caller would not inherit it.

    PROBE: drop the isinstance guard and this raises instead of degrading.
    """
    source = InstagramRoster(base_url="http://127.0.0.1:1")
    for bad in (None, 12345, object(), b"1183780976338188"):
        out = await source.roster(conversation_id=bad)  # type: ignore[arg-type]
        assert out.available is False
        assert out.members == ()
