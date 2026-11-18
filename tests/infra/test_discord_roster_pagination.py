"""The real REST reader follows provider cursors and refuses partial rosters."""

import asyncio

import pytest
from aiohttp import web

from nora.platform.discord.roster import DiscordRoster

GUILD = "100000000000000203"
ROOM = GUILD + "/100000000000000204"
START = 100000000000001000


def rows(count):
    return [{"user": {"id": str(START + i)}} for i in range(count)]


@pytest.fixture
async def api(aiohttp_server):
    state = {"rows": rows(1125), "calls": [], "second_status": 200,
             "second_body": None, "pause": None}

    async def members(request):
        assert request.headers["Authorization"] == "Bot generated-token"
        assert request.match_info["guild"] == GUILD
        assert request.query["limit"] == "1000"
        after = int(request.query.get("after", "0"))
        state["calls"].append(after)
        if after:
            if state["pause"] is not None:
                await state["pause"].wait()
            if state["second_status"] != 200:
                return web.json_response({"message": "generated-error"},
                                         status=state["second_status"])
            if state["second_body"] is not None:
                return web.json_response(state["second_body"])
        selected = [row for row in state["rows"] if int(row["user"]["id"]) > after]
        return web.json_response(selected[:1000])

    app = web.Application()
    app.router.add_get("/guilds/{guild}/members", members)
    server = await aiohttp_server(app)
    yield server, state
    if state["pause"] is not None:
        state["pause"].set()


async def test_all_pages_are_read_before_a_roster_is_available(api):
    server, state = api
    roster = await DiscordRoster("generated-token", base_url=str(server.make_url(""))).roster(
        conversation_id=ROOM,
    )
    assert roster.available is True
    assert roster.members == tuple(frozenset({str(START + i)}) for i in range(1125))
    assert state["calls"] == [0, START + 999]


async def test_an_exactly_full_last_page_needs_an_empty_page_to_establish_completion(api):
    server, state = api
    state["rows"] = rows(1000)
    roster = await DiscordRoster("generated-token", base_url=str(server.make_url(""))).roster(
        conversation_id=ROOM,
    )
    assert roster.available is True and len(roster.members) == 1000
    assert state["calls"] == [0, START + 999]


async def test_empty_complete_guild_is_distinct_from_an_unavailable_read(api):
    server, state = api
    state["rows"] = []
    roster = await DiscordRoster("generated-token", base_url=str(server.make_url(""))).roster(
        conversation_id=ROOM,
    )
    assert roster.available is True and roster.members == ()
    assert state["calls"] == [0]


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
async def test_failed_later_page_never_publishes_a_successful_prefix(api, status):
    server, state = api
    state["second_status"] = status
    roster = await DiscordRoster("generated-token", base_url=str(server.make_url(""))).roster(
        conversation_id=ROOM,
    )
    assert roster.available is False and roster.members == ()
    assert state["calls"] == [0, START + 999]


@pytest.mark.parametrize("body", [
    [{"user": {}}], [{"user": {"id": "not-a-snowflake"}}],
    [{"user": {"id": str(START)}}],
    [{"user": {"id": str(START + 1000)}}] * 2,
    {"members": []},
])
async def test_malformed_or_regressing_later_page_cannot_remove_missing_people(api, body):
    server, state = api
    state["second_body"] = body
    roster = await DiscordRoster("generated-token", base_url=str(server.make_url(""))).roster(
        conversation_id=ROOM,
    )
    assert roster.available is False and roster.members == ()
    assert state["calls"] == [0, START + 999]


async def test_a_timed_out_second_page_refuses_the_whole_roster(api):
    server, state = api
    state["pause"] = asyncio.Event()
    roster = await DiscordRoster(
        "generated-token", base_url=str(server.make_url("")), timeout_seconds=0.3,
    ).roster(conversation_id=ROOM)
    assert roster.available is False and roster.members == ()
    assert state["calls"] == [0, START + 999]
