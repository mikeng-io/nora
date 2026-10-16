"""`nora.platform.discord.mention_resolve` — turning the model's plaintext
'@Name' into a real Discord `<@id>` ping, server-side.

The model only ever writes text (`send_reply(text: str)` has no structured
mention parameter, and the transcript it reads never carries a snowflake —
`tests/test_discord_parser.py`'s inbound-resolution tests are the other half
of that). So the ONLY place that can turn '@Miles Novak' into something Discord
will actually notify on is here, against the room's live member list.
"""

from __future__ import annotations

import logging

import pytest
from aiohttp import web

from nora.domain.ports import Mention
from nora.domain.utf16 import utf16_len
from nora.platform.discord.mention_resolve import (
    fetch_named_members,
    resolve_outbound_mentions,
    splice_composed_mentions,
)

GUILD = "100000000000000203"


@pytest.fixture
async def api(aiohttp_server):
    state: dict = {"status": 200, "members": []}

    async def handler(request: web.Request) -> web.Response:
        if state["status"] != 200:
            return web.json_response({"message": "no"}, status=state["status"])
        return web.json_response(state["members"])

    app = web.Application()
    app.router.add_get("/guilds/{guild}/members", handler)
    server = await aiohttp_server(app)
    server.state = state
    return server


async def _fetch(server, guild=GUILD):
    import aiohttp

    return await fetch_named_members(
        "tok",
        base_url=str(server.make_url("")).rstrip("/"),
        guild_id=guild,
        client_timeout=aiohttp.ClientTimeout(total=2.0),
    )


# ---------------------------------------------------------------------------
# fetch_named_members
# ---------------------------------------------------------------------------


async def test_a_nickname_overrides_global_name_and_username(api):
    api.state["members"] = [
        {"user": {"id": "1", "username": "mike123", "global_name": "Miles"}, "nick": "Miles Novak"},
    ]

    named = await _fetch(api)

    assert named == {"Miles Novak": "1"}


async def test_global_name_is_used_when_there_is_no_nickname(api):
    api.state["members"] = [{"user": {"id": "1", "username": "mike123", "global_name": "Miles"}}]

    named = await _fetch(api)

    assert named == {"Miles": "1"}


async def test_username_is_the_last_resort(api):
    api.state["members"] = [{"user": {"id": "1", "username": "mike123"}}]

    named = await _fetch(api)

    assert named == {"mike123": "1"}


async def test_two_members_sharing_a_display_name_are_both_dropped(api, caplog):
    """Ambiguous — splicing either id would be a guess about which Miles was
    meant, and this codebase never guesses at an identity (R-5's shape)."""
    api.state["members"] = [
        {"user": {"id": "1", "username": "a"}, "nick": "Miles"},
        {"user": {"id": "2", "username": "b"}, "nick": "Miles"},
    ]

    with caplog.at_level(logging.WARNING, logger="nora.discord.mention_resolve"):
        named = await _fetch(api)

    assert named == {}
    assert any("Miles" in r.message for r in caplog.records)


async def test_a_forbidden_read_returns_empty_not_raises(api):
    api.state["status"] = 403

    named = await _fetch(api)

    assert named == {}


async def test_a_server_error_returns_empty_not_raises(api):
    api.state["status"] = 500

    named = await _fetch(api)

    assert named == {}


async def test_an_unreachable_api_never_raises():
    import aiohttp

    named = await fetch_named_members(
        "tok", base_url="http://127.0.0.1:1", guild_id=GUILD,
        client_timeout=aiohttp.ClientTimeout(total=0.2),
    )

    assert named == {}


async def test_a_member_row_with_no_usable_id_is_skipped(api):
    api.state["members"] = [
        {"user": {"username": "no-id"}},
        {"user": {"id": "1", "username": "ok"}},
    ]

    named = await _fetch(api)

    assert named == {"ok": "1"}


# ---------------------------------------------------------------------------
# resolve_outbound_mentions
# ---------------------------------------------------------------------------


def test_a_known_name_is_spliced_into_a_real_mention():
    text, ids = resolve_outbound_mentions("@Miles Novak 快撚啲", {"Miles Novak": "111"})

    assert text == "<@111> 快撚啲"
    assert ids == ("111",)


def test_the_longest_matching_name_wins_over_a_shorter_prefix():
    """Two members: 'Miles' and 'Miles Novak'. '@Miles Novak ...' must resolve to the
    full name, not stop short and leave ' Novak' dangling as plain text."""
    text, ids = resolve_outbound_mentions(
        "@Miles Novak bye", {"Miles": "111", "Miles Novak": "222"},
    )

    assert text == "<@222> bye"
    assert ids == ("222",)


def test_multiple_distinct_mentions_are_all_spliced():
    text, ids = resolve_outbound_mentions(
        "@Miles Novak 快啲, @Tyler Cortez 上線未", {"Miles Novak": "111", "Tyler Cortez": "222"},
    )

    assert text == "<@111> 快啲, <@222> 上線未"
    assert set(ids) == {"111", "222"}


def test_an_unrecognized_name_is_left_as_plain_text():
    """The whole point: never invent a ping for someone not on the roster."""
    text, ids = resolve_outbound_mentions("@Someone Unknown hi", {"Miles Novak": "111"})

    assert text == "@Someone Unknown hi"
    assert ids == ()


def test_no_named_members_leaves_the_text_untouched():
    text, ids = resolve_outbound_mentions("@Miles Novak hi", {})

    assert text == "@Miles Novak hi"
    assert ids == ()


def test_text_with_no_at_sign_is_returned_unchanged():
    text, ids = resolve_outbound_mentions("no mentions here", {"Miles Novak": "111"})

    assert text == "no mentions here"
    assert ids == ()


# ---------------------------------------------------------------------------
# splice_composed_mentions — the composer already resolved the id;
# this only converts UTF-16 offsets and splices.
# ---------------------------------------------------------------------------


def test_a_composed_mention_at_offset_zero_is_spliced_in():
    text = "@Tyler Cortez 柯屎係你嘅 escape plan"
    mention = Mention(
        start=0, length=utf16_len("@Tyler Cortez"), recipient="100000000000000002",
    )

    spliced, ids = splice_composed_mentions(text, (mention,))

    assert spliced == "<@100000000000000002> 柯屎係你嘅 escape plan"
    assert ids == ("100000000000000002",)
    assert spliced.count("<@100000000000000002>") == 1


def test_a_composed_mention_after_an_emoji_is_positioned_by_utf16_units():
    """The failure a Python-character (not UTF-16-unit) splice would ship: 😅
    is ONE Python character but TWO UTF-16 units, so slicing on the raw
    offset number would cut the mention one character short."""
    text = "😅 @Tyler hi"
    mention = Mention(
        start=utf16_len("😅 "), length=utf16_len("@Tyler"), recipient="9",
    )

    spliced, ids = splice_composed_mentions(text, (mention,))

    assert spliced == "😅 <@9> hi"
    assert ids == ("9",)


def test_two_composed_mentions_are_spliced_right_to_left():
    """Splicing the FIRST mention first would shift the Python string index
    the SECOND mention's offset depends on, by however many characters
    '@Grant' and '<@11111111111111>' differ in length. Right-to-left keeps
    an earlier offset valid across a later splice, which is the only order
    that cannot corrupt one mention while resolving another."""
    text = "@Grant 同 @Miles 你哋幾高?"
    grant = Mention(
        start=0, length=utf16_len("@Grant"), recipient="11111111111111",
    )
    miles = Mention(
        start=utf16_len("@Grant 同 "), length=utf16_len("@Miles"), recipient="22",
    )

    spliced, ids = splice_composed_mentions(text, (grant, miles))

    assert spliced == "<@11111111111111> 同 <@22> 你哋幾高?"
    assert ids == ("11111111111111", "22")


def test_no_mentions_leaves_the_text_untouched():
    spliced, ids = splice_composed_mentions("no mentions here", ())

    assert spliced == "no mentions here"
    assert ids == ()
