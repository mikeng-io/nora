"""The utterance forward reads Nora's answer by outcome, not by status.

Falsification: reduce the check in `forward_utterance` to `status == 200`,
or to the outcome field alone, and a bare `{"outcome": "complete"}`,
`rejected`, `unavailable`-under-200 and the disagreeing status read as
success; let it follow redirects and the 307 does; drop the transport
catch and an unreachable Nora raises.
"""

# ruff: noqa: S101, S105 - synthetic wire tests use assertions and a fake bearer.

import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from wakegate.stt import forward_utterance

TOKEN = "t" * 32
PATH = "/v1/intake/operations"


def _envelope(outcome: str, **overrides) -> dict:
    """A well-formed intake operation envelope, as the app answers."""
    state = "started" if outcome == "pending" else "settled"
    body = {
        "outcome": outcome, "payload": {"error": None},
        "when": {"state": state, "started_at_ms": 1, "settled_at_ms": None if state != "settled" else 2},
        "from": {"target": "intake", "platform_ref": None},
        "scope": {"level": "installation", "id": None}, "trace": "",
    }
    body.update(overrides)
    return body


def _answer(status: int, outcome: str | None = None, *, raw: str | None = None,
            body: dict | None = None):
    async def handler(request: web.Request) -> web.Response:
        request.app["seen"].append(
            {"headers": dict(request.headers), "body": await request.json()}
        )
        if raw is not None:
            return web.Response(status=status, text=raw, content_type="application/json")
        answer = body if body is not None else {} if outcome is None else _envelope(outcome)
        return web.Response(status=status, text=json.dumps(answer),
                            content_type="application/json")
    return handler


async def _redirect(request: web.Request) -> web.Response:
    request.app["seen"].append({"redirected": True})
    raise web.HTTPTemporaryRedirect("/elsewhere")


async def _elsewhere(request: web.Request) -> web.Response:
    request.app["seen"].append({"followed": True})
    return web.json_response(_envelope("complete"))


async def _forward(handler, **kwargs):
    app = web.Application()
    app["seen"] = []
    app.router.add_post(PATH, handler)
    app.router.add_post("/elsewhere", _elsewhere)
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            ok = await forward_utterance(
                session, nora_url=str(server.make_url("/")), user_id="u1",
                text="hi", guild_id="g1", token=TOKEN, **kwargs,
            )
    finally:
        await server.close()
    return ok, app["seen"]


async def test_forward_reads_the_outcome():
    ok, seen = await _forward(_answer(200, "complete"))
    assert ok is True
    request = seen[0]["body"]
    assert seen[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert set(request) == {"operation_id", "kind", "effect"}
    assert request["kind"] == "record"
    effect = request["effect"]
    assert set(effect) == {"event_id", "kind", "guild_id", "user_id", "text"}
    assert effect["kind"] == "utterance"
    assert (effect["guild_id"], effect["user_id"], effect["text"]) == ("g1", "u1", "hi")

    ok, again = await _forward(_answer(200, "complete"), voice_channel_id="c1")
    assert ok is True
    second = again[0]["body"]
    assert second["effect"]["voice_channel_id"] == "c1"
    assert second["operation_id"] != request["operation_id"]
    assert second["effect"]["event_id"] != effect["event_id"]

    refused = [
        _answer(200, "rejected"),
        _answer(503, "unavailable"),
        _answer(200, "unavailable"),   # status disagrees with the outcome
        _answer(403, "denied"),
        _answer(202, "pending"),
        _answer(200, "unsupported"),
        _answer(200, "incomplete"),
        _answer(200, "uncertain"),
        _answer(202, "complete"),      # status disagrees with the outcome
        _answer(400),
        _answer(200, raw="[]"),        # not an envelope
        _answer(200, raw="not json"),
        # a 200 that says complete but is not a whole envelope
        _answer(200, body={"outcome": "complete"}),
        _answer(200, body=_envelope("complete", trace=None)),
        _answer(200, body=_envelope("complete", scope={"level": "galaxy"})),
        _answer(200, body=_envelope("complete", when={"state": "started"})),
        _answer(200, body=_envelope("complete", payload=[])),
        _answer(200, body=_envelope("complete", **{"from": {}})),
    ]
    for handler in refused:
        ok, _ = await _forward(handler)
        assert ok is False

    ok, seen = await _forward(_redirect)
    assert ok is False
    assert {"followed": True} not in seen

    async with aiohttp.ClientSession() as session:
        ok = await forward_utterance(
            session, nora_url="http://127.0.0.1:9", user_id="u1", text="hi",
            guild_id="g1", token=TOKEN,
        )
    assert ok is False
