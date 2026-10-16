"""`SignalReactor` — the signal-cli `sendReaction` JSON-RPC wire shape.

The parameter spellings asserted here are not inferred from the send path;
they are read off signal-cli 0.14.7's own shipped man pages (see
`nora.platform.signal.react`'s module docstring for the citation). Getting one
of them wrong is a silent failure by construction: signal-cli answers, Nora
counts a reaction, and nothing appears on the message — which is exactly the
class of bug R-5 makes invisible, so it has to be pinned on the wire.
"""

import pytest
from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.domain.ports import ReactionResult
from nora.platform.signal.react import SignalReactor

TARGET = ConversationTarget("signal", "Z3JvdXA=", "group")


@pytest.fixture
async def rpc_server(aiohttp_server):
    calls: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        calls.append(body)
        return web.json_response({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    server.calls = calls
    return server


def _reactor(server) -> SignalReactor:
    return SignalReactor(str(server.make_url("")).rstrip("/"))


async def test_react_posts_the_documented_sendreaction_shape(rpc_server):
    """Pins the wire spellings `sendReaction`/`groupId`/`emoji`/`targetAuthor`/
    `targetTimestamp` used by `nora/platform/signal/react.py`."""
    result = await _reactor(rpc_server).react(
        TARGET, "\N{EYES}", to_author="+85211122233", to_timestamp=1754300000000,
        request_id="tag-1",
    )
    assert result == ReactionResult(ok=True, supported=True)
    body = rpc_server.calls[0]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "sendReaction"
    assert body["id"] == "tag-1"
    assert body["params"] == {
        "groupId": "Z3JvdXA=",
        "emoji": "\N{EYES}",
        "targetAuthor": "+85211122233",
        "targetTimestamp": 1754300000000,
        "remove": False,
    }


async def test_a_sender_behind_phone_number_privacy_reacts_under_their_aci(rpc_server):
    """signal-cli accepts a bare ACI UUID wherever a RECIPIENT is expected, so
    the `sourceUuid` the parser falls back to is a usable `targetAuthor` with no
    extra resolution step. This is the case that would otherwise be shipped
    silently broken for anyone with a hidden phone number."""
    aci = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"
    result = await _reactor(rpc_server).react(
        TARGET, "\N{EYES}", to_author=aci, to_timestamp=1754300000000,
    )
    assert result.ok is True
    assert rpc_server.calls[0]["params"]["targetAuthor"] == aci


async def test_remove_sets_the_remove_flag(rpc_server):
    await _reactor(rpc_server).react(
        TARGET, "\N{EYES}", to_author="+1", to_timestamp=7, remove=True,
    )
    assert rpc_server.calls[0]["params"]["remove"] is True


async def test_a_missing_target_author_is_refused_without_a_round_trip(rpc_server):
    result = await _reactor(rpc_server).react(
        TARGET, "\N{EYES}", to_author="", to_timestamp=7,
    )
    assert result == ReactionResult(ok=False, supported=True)
    assert rpc_server.calls == []


async def test_a_missing_target_timestamp_is_refused_without_a_round_trip(rpc_server):
    result = await _reactor(rpc_server).react(
        TARGET, "\N{EYES}", to_author="+1", to_timestamp=0,
    )
    assert result.ok is False
    assert rpc_server.calls == []


async def test_a_dm_is_refused(rpc_server):
    dm = ConversationTarget("signal", "+1", "dm")
    result = await _reactor(rpc_server).react(dm, "\N{EYES}", to_author="+1", to_timestamp=7)
    assert result.ok is False
    assert rpc_server.calls == []


async def test_an_http_error_is_a_supported_failure_not_an_exception(aiohttp_server):
    """`supported` stays True: signal-cli HAS reactions, this attempt just did
    not land. Collapsing the two would make a wedged daemon and a platform
    without reactions read identically in `nora_reactions_total`."""
    async def handler(request):
        return web.Response(status=500, text="boom")

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    result = await _reactor(server).react(
        TARGET, "\N{EYES}", to_author="+1", to_timestamp=7,
    )
    assert result == ReactionResult(ok=False, supported=True)


async def test_a_jsonrpc_error_body_is_a_failure(aiohttp_server):
    async def handler(request):
        return web.json_response({"jsonrpc": "2.0", "error": {"code": -32602}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    server = await aiohttp_server(app)
    result = await _reactor(server).react(TARGET, "\N{EYES}", to_author="+1", to_timestamp=7)
    assert result.ok is False


async def test_an_unreachable_daemon_never_raises():
    """R-5: the reaction path must absorb transport failure entirely, because
    its caller runs detached and has no one to report to."""
    result = await SignalReactor("http://127.0.0.1:1", timeout=0.5).react(
        TARGET, "\N{EYES}", to_author="+1", to_timestamp=7,
    )
    assert result == ReactionResult(ok=False, supported=True)
