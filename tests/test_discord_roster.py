"""`DiscordRoster` — the guild members read the room-scoped tools resolve in.

The shared behaviour lives in `tests/contract/test_roster_source_contract.py`,
which this implementation passes alongside `SignalRoster` and the fake.
What is here is what only Discord has: four distinct REFUSAL branches, each of
which must answer `available=False` rather than an empty roster.

That distinction is the whole safety property. `Roster.available` exists
because an unreachable source and an empty room would otherwise be the same
value, and every room-scoped tool would answer «there is nobody here» when the
truth is «I could not look». Under R-5 the second is the only honest answer,
and it is the one an operator can act on.

The fake below answers like the REAL endpoint, including its wrong-call shapes
— 403 for a missing privileged intent, 404 for a guild the bot is not in, 500
for an outage — rather than one status for everything.
"""

from __future__ import annotations

import logging

import pytest
from aiohttp import web

from nora.platform.discord.roster import MAX_MEMBERS_PER_PAGE, DiscordRoster

GUILD = "100000000000000203"
CHANNEL = "100000000000000204"
ROOM = f"{GUILD}/{CHANNEL}"


@pytest.fixture
async def api(aiohttp_server):
    """A stand-in for `GET /guilds/{id}/members` that distinguishes its own
    failure modes instead of answering everything identically."""
    state: dict = {"status": 200, "members": [], "calls": [], "body": None}

    async def handler(request: web.Request) -> web.Response:
        state["calls"].append(request.match_info["guild"])
        if state["status"] != 200:
            return web.json_response({"message": "no"}, status=state["status"])
        if state["body"] is not None:
            return web.json_response(state["body"])
        return web.json_response(state["members"])

    app = web.Application()
    app.router.add_get("/guilds/{guild}/members", handler)
    server = await aiohttp_server(app)
    server.state = state
    return server


def _roster(server) -> DiscordRoster:
    return DiscordRoster("tok", base_url=str(server.make_url("")).rstrip("/"))


async def test_the_guild_half_of_the_conversation_id_is_what_is_read(api):
    """`conversation_id` is `"<guild_id>/<channel_id>"`. Discord exposes no
    per-channel membership endpoint, so the GUILD is what can be read — and
    addressing the channel id here would 404 against a guild route."""
    api.state["members"] = [{"user": {"id": "1"}}]

    roster = await _roster(api).roster(conversation_id=ROOM)

    assert roster.available is True
    assert api.state["calls"] == [GUILD], "the guild half, not the channel half"


async def test_one_frozenset_per_member_holding_the_snowflake(api):
    """One id form per account, and it is the immutable snowflake. The username
    is deliberately absent — it is not a matchable identity on any platform,
    because its owner can change it."""
    api.state["members"] = [
        {"user": {"id": "100000000000000001", "username": "someone"}},
        {"user": {"id": "100000000000000002", "username": "other"}},
    ]

    roster = await _roster(api).roster(conversation_id=ROOM)

    assert set(roster.members) == {
        frozenset({"100000000000000001"}),
        frozenset({"100000000000000002"}),
    }


async def test_a_forbidden_read_names_the_missing_intent_in_the_log(api, caplog):
    """403 — the GUILD_MEMBERS privileged intent is off for the application.

    The single most likely Discord misconfiguration, and the one where an empty
    roster would be catastrophic: every room-scoped tool would report the room
    as containing nobody, and `where_is`'s disclosure gate would be evaluating
    a room it thinks is empty.

    The RETURN VALUE is deliberately not what this test pins — 403 falls
    through to the same `available=False` a 500 gives, and asserting only that
    would pass with the whole branch deleted. What the branch buys is the
    DIAGNOSTIC, and `docs/runbook.md` tells an operator to grep for exactly
    this line when every room-scoped tool has gone quiet on Discord while
    `/health` says the gateway is fine. That makes the string a contract.
    """
    api.state["status"] = 403

    with caplog.at_level(logging.ERROR, logger="nora.discord.roster"):
        roster = await _roster(api).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()
    assert any(
        "roster read forbidden" in r.message and "GUILD_MEMBERS" in r.message
        for r in caplog.records
    ), "the 403 must name the privileged intent — the runbook greps for it"


async def test_an_http_error_is_unavailable_not_an_empty_room(api):
    api.state["status"] = 500

    roster = await _roster(api).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()


async def test_a_guild_too_large_to_read_in_one_page_refuses_the_whole_read(api):
    """The read does not paginate, so a full page means members were LEFT OUT.

    Answering with the truncated set would UNDER-report the room — the one
    direction this module must never fail in, since a roster that has quietly
    forgotten people is a roster the disclosure gate will happily approve.
    """
    api.state["members"] = [{"user": {"id": str(i)}} for i in range(MAX_MEMBERS_PER_PAGE)]

    roster = await _roster(api).roster(conversation_id=ROOM)

    assert roster.available is False
    assert roster.members == ()


@pytest.mark.parametrize("bad", ["", "no-slash", "/100000000000000205", "100000000000000203/"])
async def test_a_malformed_conversation_id_is_unavailable_and_never_reaches_the_network(
    api, bad,
):
    """A wiring fault, not an empty room — and it must not be attempted."""
    roster = await _roster(api).roster(conversation_id=bad)

    assert roster.available is False
    assert api.state["calls"] == []


async def test_an_unreachable_api_never_raises():
    """`RosterSource.roster` is documented never to raise: the caller is a tool
    inside a live turn, and an exception there has nobody to report to."""
    roster = await DiscordRoster("tok", base_url="http://127.0.0.1:1", timeout_seconds=0.2).roster(
        conversation_id=ROOM
    )

    assert roster.available is False


async def test_a_non_list_body_is_unavailable(api):
    """Discord answering an object where a list was expected is a shape change,
    not an empty guild."""
    api.state["body"] = {"message": "unexpected"}

    roster = await _roster(api).roster(conversation_id=ROOM)

    assert roster.available is False


async def test_a_member_row_with_no_usable_id_is_reported_not_silently_dropped(api, caplog):
    """The one branch that can drop a REAL member.

    Every other refusal here answers `available=False`, so the failure is
    visible in the caller. This one keeps the roster and returns fewer people —
    UNDER-reporting, which this module's docstring calls the unsafe direction,
    because a roster that has quietly forgotten somebody is one the disclosure
    gate will happily approve. It must therefore log what it drops: the only
    branch capable of a silent wrong answer cannot be the one with no trace.
    """
    api.state["members"] = [
        {"user": {"id": "100000000000000001"}},
        {"user": {}},                      # no id at all
        {"user": {"id": 12345}},           # not a string
        {"not_a_user": {}},                # wrong shape entirely
    ]

    with caplog.at_level(logging.ERROR, logger="nora.discord.roster"):
        roster = await _roster(api).roster(conversation_id=ROOM)

    assert roster.members == (frozenset({"100000000000000001"}),)
    assert any("skipped 3 member row" in r.message for r in caplog.records), (
        "an under-reported room must say so"
    )
