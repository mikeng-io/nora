"""`DiscordReactor` — the 👀/✅/⚠️ path on Discord.

The fake below answers like the REAL endpoint, including its wrong-call shapes
(CLAUDE.md: a fake that answers every request the same way is decorative). It
returns 204 for a well-formed PUT/DELETE, 404 for deleting a reaction that was
never placed, 429 when told to, and 403 for a channel the bot cannot touch —
so a test can tell "it worked" from "it was refused" from "it was throttled".
"""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.platform.discord.react import DiscordReactor

GUILD = "100000000000000203"
CHANNEL = "100000000000000204"
MESSAGE = "100000000000000208"
EYES = "\U0001f440"


def _target() -> ConversationTarget:
    return ConversationTarget("discord", f"{GUILD}/{CHANNEL}", "group")


@pytest.fixture
async def api(aiohttp_server):
    """A stand-in for Discord's reaction endpoint that distinguishes its own
    failure modes rather than answering everything identically."""

    state: dict = {
        "calls": [], "status": None, "placed": set(),
        # 10014 Unknown Emoji — the benign "already gone" case, which is what
        # `indicate` hits on every ordinary turn.
        "not_found_code": 10014,
    }

    async def handler(request: web.Request) -> web.Response:
        emoji = unquote(request.match_info["emoji"])
        state["calls"].append((
            request.method, request.match_info["channel"],
            request.match_info["message"], emoji,
            request.headers.get("Authorization", ""),
        ))
        if state["status"] is not None:
            return web.json_response({"retry_after": 1.5}, status=state["status"])
        if request.method == "PUT":
            state["placed"].add(emoji)
            return web.Response(status=204)
        # DELETE: 404 when there is nothing to remove — and the real API says
        # WHICH 404 in the body's `code`. Serving one shape here would model the
        # assumption rather than test it: 10014 (already gone) and 10008/10003
        # (the message or channel could not be addressed) share a status and
        # mean opposite things.
        if emoji not in state["placed"]:
            return web.json_response(
                {"message": "Unknown Emoji", "code": state["not_found_code"]},
                status=404,
            )
        state["placed"].discard(emoji)
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route(
        "*",
        "/channels/{channel}/messages/{message}/reactions/{emoji}/@me",
        handler,
    )
    server = await aiohttp_server(app)
    server.state = state
    return server


def _reactor(server) -> DiscordReactor:
    return DiscordReactor("tok", base_url=str(server.make_url("")).rstrip("/"))


async def test_a_reaction_is_placed_on_the_message_by_its_snowflake(api):
    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE,
    )

    assert result.ok is True
    assert result.supported is True
    method, channel, message, emoji, auth = api.state["calls"][0]
    assert method == "PUT"
    # The CHANNEL half of "<guild>/<channel>", never the guild — addressing the
    # guild id would 404 against a channel endpoint.
    assert channel == CHANNEL
    assert message == MESSAGE
    assert emoji == EYES, "the emoji must survive percent-encoding intact"
    assert auth == "Bot tok"


async def test_removing_a_reaction_uses_delete(api):
    reactor = _reactor(api)
    await reactor.react(target=_target(), emoji=EYES, to_message_id=MESSAGE)

    result = await reactor.react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE, remove=True,
    )

    assert result.ok is True
    assert [c[0] for c in api.state["calls"]] == ["PUT", "DELETE"]


async def test_removing_a_reaction_that_was_never_placed_is_a_success(api):
    """`indicate` retracts the 👀 before every terminal emoji, including on
    paths where it never landed. Discord answers 404 there, and reporting that
    as a failure would count a normal turn as broken every time."""
    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE, remove=True,
    )

    assert result.ok is True
    assert api.state["calls"][0][0] == "DELETE"


async def test_no_message_id_is_unsupported_not_a_failure(api):
    """The honest three-state (R-27 applied to decoration): with no snowflake
    there is nothing to react to, and that is a different fact from an attempt
    that failed. It must also not reach the network."""
    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id="",
    )

    assert result.ok is False
    assert result.supported is False
    assert api.state["calls"] == [], "a call with no target must not be attempted"


async def test_a_refused_reaction_is_a_failure_but_still_supported(api):
    """403 — the bot lacks Add Reactions in that channel. `supported` stays
    True: the platform can react, this attempt was refused, and collapsing the
    two would make a permissions problem read as "Discord cannot do this"."""
    api.state["status"] = 403

    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE,
    )

    assert result.ok is False
    assert result.supported is True


async def test_rate_limiting_is_reported_as_a_failure_never_raised(api):
    api.state["status"] = 429

    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE,
    )

    assert result.ok is False
    assert result.supported is True


async def test_an_unreachable_api_never_raises():
    """R-5: decoration failing is never allowed to become the tag's problem.
    `indicate` runs this detached and catches, but the contract is that this
    returns rather than raising in the first place."""
    reactor = DiscordReactor("tok", base_url="http://127.0.0.1:1", timeout=0.2)

    result = await reactor.react(target=_target(), emoji=EYES, to_message_id=MESSAGE)

    assert result.ok is False
    assert result.supported is True


async def test_a_delete_404_that_means_the_message_is_gone_is_not_a_success(api):
    """The three 404s are not one fact.

    `indicate` retracts the 👀 before every terminal emoji, so a 404 meaning
    «that emoji was not on that message» is the normal path and must count as
    success. A 404 meaning «no such message» or «no such channel» is a wrong
    `to_message_id` or a wrong channel id — reporting that as a clean
    retraction counted a broken target as `status="sent"`.
    """
    api.state["not_found_code"] = 10008        # Unknown Message

    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE, remove=True,
    )

    assert result.ok is False
    assert result.supported is True


async def test_a_delete_404_with_no_code_is_not_assumed_benign(api):
    """A 404 whose body carries no `code` is a 404 nothing has established the
    meaning of, so it must not inherit the benign case's success. Fail closed:
    the alternative is treating an unknown failure as a clean retraction."""
    api.state["not_found_code"] = None

    result = await _reactor(api).react(
        target=_target(), emoji=EYES, to_message_id=MESSAGE, remove=True,
    )

    assert result.ok is False
    assert result.supported is True
