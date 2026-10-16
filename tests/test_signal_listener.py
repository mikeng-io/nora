import asyncio

import pytest
from aiohttp import web

from nora.platform.signal.listener import SignalListener


async def _sse_app(lines: list[str], *, close_after: bool = True):
    async def handler(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for line in lines:
            await resp.write(line.encode())
        if close_after:
            await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/api/v1/events", handler)
    return app


async def test_yields_parsed_events(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {"method":"receive","params":{"a":1}}\n\n',
         'data: {"method":"receive","params":{"a":2}}\n\n']
    ))
    got: list[dict] = []
    listener = SignalListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["params"]["a"] for e in got] == [1, 2]


async def test_a_malformed_data_line_is_skipped_not_fatal(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {not json\n\n', 'data: {"method":"receive","params":{"a":9}}\n\n']
    ))
    got: list[dict] = []
    listener = SignalListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["params"]["a"] for e in got] == [9]


async def test_a_raising_callback_does_not_kill_the_stream(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {"method":"receive","params":{"a":1}}\n\n',
         'data: {"method":"receive","params":{"a":2}}\n\n']
    ))
    seen = []

    async def on_event(event):
        seen.append(event)
        raise RuntimeError("handler exploded")

    listener = SignalListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(on_event)
    assert len(seen) == 2


async def test_last_event_at_advances(aiohttp_server):
    clock = iter([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    server = await aiohttp_server(await _sse_app(['data: {"method":"receive"}\n\n']))
    listener = SignalListener(str(server.make_url("")).rstrip("/"),
                              sleeper=_stop_after(1), clock=lambda: next(clock))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))
    assert listener.last_event_at is not None


async def test_a_keepalive_comment_advances_last_event_at_without_reaching_on_event(aiohttp_server):
    """signal-cli sends a `:` comment line about every 18s. It is the only
    evidence a QUIET room's socket is alive, so it must advance
    `last_event_at` — and it is not a message, so it must never reach the
    parse/deliver path."""
    server = await aiohttp_server(await _sse_app([':\n', ':\n']))
    got: list[dict] = []
    listener = SignalListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert got == []
    assert listener.last_event_at is not None


async def test_a_blank_data_line_is_not_liveness(aiohttp_server):
    """Only a comment line is a keep-alive; an empty `data:` is neither a
    message nor evidence the daemon is talking."""
    server = await aiohttp_server(await _sse_app(['data:\n\n']))
    listener = SignalListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))
    assert listener.last_event_at is None


def test_backoff_is_exponential_capped_and_jittered():
    listener = SignalListener("http://x", max_backoff=60.0)
    delays = [listener._next_backoff(attempt) for attempt in range(0, 10)]
    assert all(0 <= d <= 60.0 for d in delays)
    assert max(delays[:1]) <= 1.0
    assert delays[-1] <= 60.0
    # full jitter: two draws at the same attempt are (almost always) different
    assert len({listener._next_backoff(6) for _ in range(20)}) > 1


async def test_reconnects_after_the_stream_closes(aiohttp_server):
    connections = {"n": 0}

    async def handler(request):
        connections["n"] += 1
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"method":"receive"}\n\n')
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/api/v1/events", handler)
    server = await aiohttp_server(app)
    listener = SignalListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(3))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))
    assert connections["n"] >= 2


# --- helpers -------------------------------------------------------------
class _Stop(Exception):
    """Breaks the listener's infinite loop deterministically, without sleeping."""


def _stop_after(n: int):
    state = {"n": 0}

    async def sleeper(delay: float) -> None:
        state["n"] += 1
        if state["n"] >= n:
            raise _Stop
    return sleeper


async def _record(bucket: list, event: dict) -> None:
    bucket.append(event)
