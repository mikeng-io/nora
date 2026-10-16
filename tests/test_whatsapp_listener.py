"""`nora.platform.whatsapp.listener.WhatsAppListener` — the SSE client,
mirroring `tests/test_signal_listener.py`'s coverage, plus the two
WhatsApp-specific event types the listener owns (heartbeat, dropped)."""

import asyncio
import logging

import pytest
from aiohttp import web

from nora.platform.whatsapp.listener import WhatsAppListener


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
    app.router.add_get("/events", handler)
    return app


async def test_yields_parsed_message_events(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {"type":"message","id":"a"}\n\n',
         'data: {"type":"message","id":"b"}\n\n']
    ))
    got: list[dict] = []
    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["id"] for e in got] == ["a", "b"]


async def test_a_malformed_data_line_is_skipped_not_fatal(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {not json\n\n', 'data: {"type":"message","id":"c"}\n\n']
    ))
    got: list[dict] = []
    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["id"] for e in got] == ["c"]


async def test_a_raising_callback_does_not_kill_the_stream(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {"type":"message","id":"a"}\n\n',
         'data: {"type":"message","id":"b"}\n\n']
    ))
    seen = []

    async def on_event(event):
        seen.append(event)
        raise RuntimeError("handler exploded")

    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(on_event)
    assert len(seen) == 2


async def test_last_event_at_advances_on_any_parsed_event(aiohttp_server):
    server = await aiohttp_server(await _sse_app(['data: {"type":"message","id":"a"}\n\n']))
    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))
    assert listener.last_event_at is not None


async def test_a_heartbeat_updates_last_event_at_without_reaching_on_event(aiohttp_server):
    """The heartbeat IS the `/health` `last_event_age_seconds` detector's
    evidence in a quiet room — it must advance `last_event_at` — but it is
    not a message and must never reach the parse/deliver path."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"type":"heartbeat","ts_ms":1,"connected":true}\n\n']
    ))
    got: list[dict] = []
    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert got == []
    assert listener.last_event_at is not None


async def test_a_dropped_event_logs_a_warning_and_is_not_delivered(aiohttp_server, caplog):
    """Audit rows were lost upstream and nothing here can recover them —
    drop-and-count — so it is LOUD, and it is not a
    message."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"type":"dropped","dropped_total":17}\n\n',
         'data: {"type":"message","id":"a"}\n\n']
    ))
    got: list[dict] = []
    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with (
        caplog.at_level(logging.WARNING, logger="nora.whatsapp.listener"),
        pytest.raises(_Stop),
    ):
        await listener.listen(lambda e: _record(got, e))
    assert [e["id"] for e in got] == ["a"]
    assert "17" in caplog.text
    assert "dropped" in caplog.text


async def test_reconnects_after_the_stream_closes(aiohttp_server):
    connections = {"n": 0}

    async def handler(request):
        connections["n"] += 1
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"type":"heartbeat","ts_ms":1}\n\n')
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/events", handler)
    server = await aiohttp_server(app)
    listener = WhatsAppListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(3))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))
    assert connections["n"] >= 2
    assert listener.connected is False


def test_backoff_is_exponential_capped_and_jittered():
    listener = WhatsAppListener("http://x", max_backoff=60.0)
    delays = [listener._next_backoff(attempt) for attempt in range(0, 10)]
    assert all(0 <= d <= 60.0 for d in delays)
    assert max(delays[:1]) <= 1.0
    assert delays[-1] <= 60.0
    # full jitter: two draws at the same attempt are (almost always) different
    assert len({listener._next_backoff(6) for _ in range(20)}) > 1


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
