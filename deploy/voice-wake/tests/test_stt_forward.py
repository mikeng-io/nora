"""The utterance forward reads Nora's answer by outcome, not by status,
and reads a lost or unsettled answer back instead of guessing.

Falsification: accept any body as an envelope and a bare
`{"outcome": "complete"}`, a string `payload.error` or a `room` scope reads
as delivered, and an array `outcome` raises; reduce the check to the status
and `rejected` under 200 does; follow redirects and the 307 does; drop the
read-back and a dropped answer after Nora recorded the utterance, or a
`pending` that settles, is lost; give each request its own timeout and a
stalled Nora holds the utterance four times as long; let the POST use the
whole budget and a stalled POST leaves no time to read back; drop the
`traceparent` and the POST and its read-back carry no trace.
"""

# ruff: noqa: S101, S105 - synthetic wire tests use assertions and a fake bearer.

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

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


async def _forward(handler, *, reads=(), **kwargs):
    """Drive one forward; `reads` answer the read-backs in order, then `nothing_found`."""
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
        _answer(200, body=_envelope("complete", payload={"error": "boom"})),
        _answer(200, body=_envelope("complete", scope={"level": "room", "id": "r1"})),
        _answer(200, body={**_envelope("complete"), "outcome": ["complete"]}),
        _answer(200, body={**_envelope("complete"), "outcome": {"complete": True}}),
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


async def _recorded_then_dropped(request: web.Request) -> web.Response:
    request.app["seen"].append({"body": await request.json()})
    request.transport.close()  # Nora recorded it; the answer never arrives
    return web.json_response(_envelope("complete"))


async def test_a_lost_or_unsettled_answer_is_read_back():
    done = _read("ok", _envelope("complete"))

    # recorded, then the answer was lost: the read-back finds it complete
    ok, seen = await _forward(_recorded_then_dropped, reads=[done])
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

    # a malformed read is one failed read: the next one still counts
    ok_body = _read("ok", _envelope("complete"))[1]
    bad_reads = (
        (200, {**ok_body, "when": {"observed_at_ms": True, "known_by": "bridge_clock"}}),
        (200, {**ok_body, "from": {"source": "intake", "healthy": True,
                                   "failed_sources": "db"}}),
        (200, {**ok_body, "outcome": ["ok"]}),
        (200, {**ok_body, "scope": {"level": "room"}}),
        _read("ok", _envelope("complete", scope={"level": "room"})),
        _read("ok", _envelope("complete", payload={"error": "boom"})),
    )
    for bad in bad_reads:
        ok, seen = await _forward(_answer(202, "pending"), reads=[bad, done])
        assert ok is True
        assert len(_gets(seen)) == 2

    # a read-back that is not a whole read envelope proves nothing
    for bad in ((200, {"outcome": "ok", "payload": _envelope("complete")}),
                _read("ok", {"outcome": "complete"}),
                _read("ok", _envelope("complete"), status=503)):
        ok, _ = await _forward(_answer(202, "pending"), reads=[bad] * 3)
        assert ok is False


async def _stall(request: web.Request) -> web.Response:
    request.app["seen"].append({"get": request.match_info.get("operation_id")})
    await asyncio.sleep(1.0)
    return web.json_response(_envelope("complete"))


async def _timed_forward(app: web.Application, budget: float):
    server = TestServer(app)
    await server.start_server()
    try:
        timeout = aiohttp.ClientTimeout(total=budget)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            started = time.monotonic()
            ok = await forward_utterance(
                session, nora_url=str(server.make_url("/")), user_id="u1",
                text="hi", guild_id="g1", token=TOKEN,
            )
            elapsed = time.monotonic() - started
    finally:
        await server.close()
    return ok, elapsed


async def test_one_deadline_covers_the_post_and_every_read_back():
    app = web.Application()
    app["seen"] = []
    app.router.add_post(PATH, _stall)
    app.router.add_get(PATH + "/{operation_id}", _stall)
    ok, elapsed = await _timed_forward(app, 0.3)
    assert ok is False
    assert elapsed <= 0.3 + 0.1
    assert [entry for entry in app["seen"] if entry["get"]], "no read-back was attempted"


async def test_a_stalled_post_leaves_time_to_read_back():
    app = web.Application()
    app["posts"], app["gets"] = 0, 0

    async def recorded_then_stalled(request: web.Request) -> web.Response:
        request.app["posts"] += 1
        await asyncio.sleep(1.0)  # recorded; the answer is held past the POST's share
        return web.json_response(_envelope("complete"))

    async def settled(request: web.Request) -> web.Response:
        request.app["gets"] += 1
        status, body = _read("ok", _envelope("complete"))
        return web.json_response(body, status=status)

    app.router.add_post(PATH, recorded_then_stalled)
    app.router.add_get(PATH + "/{operation_id}", settled)
    ok, elapsed = await _timed_forward(app, 0.4)
    assert ok is True
    assert (app["posts"], app["gets"] >= 1) == (1, True)
    assert elapsed <= 0.4 + 0.1
async def test_the_warning_says_whether_delivery_is_unknown(caplog):
    still = _read("ok", _envelope("pending"))
    await _forward(_answer(202, "pending"), reads=[still] * 3)
    assert "delivery unconfirmed (pending)" in caplog.text
    caplog.clear()
    await _forward(_answer(200, "rejected"))
    assert "did not record the utterance (rejected)" in caplog.text
    assert "unconfirmed" not in caplog.text


def test_the_shipped_schema_is_the_contract_document():
    check = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts" / "intake_schema.py"), "--check"],
        capture_output=True, text=True, check=False,
    )
    assert check.returncode == 0, check.stderr
