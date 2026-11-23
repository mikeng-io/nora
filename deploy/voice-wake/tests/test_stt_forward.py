"""The utterance forward reads Nora's answer by outcome, not by status,
and reads a lost or unsettled answer back instead of guessing.

Falsification: reduce the check in `forward_utterance` to `status == 200`,
or to the outcome field alone, and a bare `{"outcome": "complete"}`,
`rejected`, `unavailable`-under-200 and the disagreeing status read as
success; let it follow redirects and the 307 does; drop the transport
catch and an unreachable Nora raises; drop the read-back and a timeout
after Nora recorded the utterance, or a `pending` that settles, is lost;
drop the `traceparent` and the POST and its read-back carry no trace.
"""

# ruff: noqa: S101, S105 - synthetic wire tests use assertions and a fake bearer.

import asyncio
import json
import re

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from wakegate.stt import forward_utterance

TOKEN = "t" * 32
TRACEPARENT = re.compile(r"00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-01")
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


def _read(outcome: str, payload: dict | None = None, status: int | None = None):
    """A read-back answer: a read envelope around the record's envelope."""
    body = {
        "outcome": outcome, "payload": payload,
        "when": {"observed_at_ms": 3, "known_by": "bridge_clock"},
        "from": {"source": "intake", "healthy": True, "failed_sources": []},
        "scope": {"level": "installation", "id": None}, "trace": "",
    }
    return (status or (503 if outcome == "unavailable" else 200)), body


async def _forward(handler, *, reads=(), timeout: float | None = None, **kwargs):
    """Drive one forward; `reads` are the read-back answers in order, then
    `nothing_found`."""
    app = web.Application()
    app["seen"] = []
    answers = list(reads)

    async def read_back(request: web.Request) -> web.Response:
        request.app["seen"].append({"get": request.match_info["operation_id"],
                                    "headers": dict(request.headers)})
        status, body = answers.pop(0) if answers else _read("nothing_found")
        return web.Response(status=status, text=json.dumps(body),
                            content_type="application/json")

    app.router.add_post(PATH, handler)
    app.router.add_get(PATH + "/{operation_id}", read_back)
    app.router.add_post("/elsewhere", _elsewhere)
    server = TestServer(app)
    await server.start_server()
    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else None
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
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
    assert TRACEPARENT.fullmatch(seen[0]["headers"]["traceparent"])
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
    assert again[0]["headers"]["traceparent"] != seen[0]["headers"]["traceparent"]

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

    # answers that settle the forward on the one POST: no read-back
    for handler in (_answer(200, "rejected"), _answer(403, "denied"),
                    _answer(200, "incomplete"), _answer(409), _answer(401)):
        ok, seen = await _forward(handler, reads=[_read("ok", _envelope("complete"))])
        assert ok is False
        assert [entry for entry in seen if "get" in entry] == []

    ok, seen = await _forward(_redirect)
    assert ok is False
    assert {"followed": True} not in seen

    async with aiohttp.ClientSession() as session:
        ok = await forward_utterance(
            session, nora_url="http://127.0.0.1:9", user_id="u1", text="hi",
            guild_id="g1", token=TOKEN,
        )
    assert ok is False


def _gets(seen: list[dict]) -> list[dict]:
    return [entry for entry in seen if "get" in entry]


async def _slow_complete(request: web.Request) -> web.Response:
    request.app["seen"].append({"body": await request.json()})
    await asyncio.sleep(0.5)  # the answer is lost to the client's timeout
    return web.json_response(_envelope("complete"))


async def test_a_lost_or_unsettled_answer_is_read_back():
    done = _read("ok", _envelope("complete"))

    # recorded, then the answer was lost: the read-back finds it complete
    ok, seen = await _forward(_slow_complete, reads=[done], timeout=0.2)
    assert ok is True
    assert _gets(seen)[0]["get"] == seen[0]["body"]["operation_id"]

    # an answer that is no envelope is lost too
    ok, seen = await _forward(_answer(502, raw="bad gateway"), reads=[done])
    assert ok is True

    # pending settles complete on the second read
    ok, seen = await _forward(_answer(202, "pending"),
                              reads=[_read("ok", _envelope("pending")), done])
    assert ok is True
    assert len(_gets(seen)) == 2
    traces = {entry["headers"]["traceparent"] for entry in seen}
    assert len(traces) == 1 and TRACEPARENT.fullmatch(traces.pop())

    # uncertain is read back; a settled uncertain record stays undelivered
    ok, seen = await _forward(_answer(200, "uncertain"),
                              reads=[_read("ok", _envelope("uncertain"))])
    assert ok is False
    assert len(_gets(seen)) == 1

    # Nora never recorded it
    ok, seen = await _forward(_answer(202, "pending"))
    assert ok is False
    assert len(_gets(seen)) == 1

    # never settles: the read-back is bounded
    still = _read("ok", _envelope("pending"))
    ok, seen = await _forward(_answer(202, "pending"), reads=[still] * 10)
    assert ok is False
    assert len(_gets(seen)) == 3

    # a read-back that is not a whole read envelope proves nothing
    for bad in ((200, {"outcome": "ok", "payload": _envelope("complete")}),
                _read("ok", {"outcome": "complete"}),
                _read("ok", _envelope("complete"), status=503)):
        ok, _ = await _forward(_answer(202, "pending"), reads=[bad] * 3)
        assert ok is False
