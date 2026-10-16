"""`nora.platform.instagram.listener.InstagramListener` — the SSE client.

Mirrors `tests/test_whatsapp_listener.py`'s coverage on the shared half (the
stream, the backoff, one bad line skipped, a raising handler not killing the
stream) and adds the half only this bridge has: a heartbeat that carries the
SESSION STATE, which is the only runtime view of a checkpoint that happens after
startup.

The discriminator is `kind`, not WhatsApp's `type` — every event body below
uses the Instagram spelling on purpose, because a listener that kept reading
`type` would swallow nothing and deliver heartbeats into the parse path.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from aiohttp import web

from nora.platform.instagram.listener import InstagramListener


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
        ['data: {"kind":"message","message_id":"a"}\n\n',
         'data: {"kind":"message","message_id":"b"}\n\n']
    ))
    got: list[dict] = []
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["message_id"] for e in got] == ["a", "b"]


async def test_the_contracts_OTHER_kinds_reach_on_event_rather_than_being_swallowed(
    aiohttp_server,
):
    """`reaction`, `edit`, `unsend` and `typing` are part of the contract and
    are NOT this module's business. The parser answers None for each, which is
    what keeps it total on its own instead of relying on a pre-filter here."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"reaction","message_id":"a"}\n\n',
         'data: {"kind":"typing","thread_id":"x"}\n\n']
    ))
    got: list[dict] = []
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["kind"] for e in got] == ["reaction", "typing"]


async def test_a_malformed_data_line_is_skipped_not_fatal(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {not json\n\n', 'data: {"kind":"message","message_id":"c"}\n\n']
    ))
    got: list[dict] = []
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert [e["message_id"] for e in got] == ["c"]


async def test_a_raising_callback_does_not_kill_the_stream(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"message","message_id":"a"}\n\n',
         'data: {"kind":"message","message_id":"b"}\n\n']
    ))
    seen = []

    async def on_event(event):
        seen.append(event)
        raise RuntimeError("handler exploded")

    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(on_event)
    assert len(seen) == 2


async def test_a_heartbeat_updates_last_event_at_without_reaching_on_event(aiohttp_server):
    """The heartbeat IS the socket-death detector — a quiet room and a dead socket are
    indistinguishable without it — so it must
    advance `last_event_at` and must never reach the parse/deliver path."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"heartbeat","ts_ms":1,"connected":true,"session":"authenticated"}\n\n']
    ))
    got: list[dict] = []
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: _record(got, e))
    assert got == []
    assert listener.last_event_at is not None


async def test_a_dropped_event_logs_a_warning_and_is_not_delivered(aiohttp_server, caplog):
    """Audit rows were lost upstream and nothing here can recover them, so it
    is LOUD, and it is not a message."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"dropped","dropped_total":17}\n\n',
         'data: {"kind":"message","message_id":"a"}\n\n']
    ))
    got: list[dict] = []
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with (
        caplog.at_level(logging.WARNING, logger="nora.instagram.listener"),
        pytest.raises(_Stop),
    ):
        await listener.listen(lambda e: _record(got, e))
    assert [e["message_id"] for e in got] == ["a"]
    assert "17" in caplog.text
    assert "dropped" in caplog.text


# --- the session state a heartbeat carries ---------------


def test_no_heartbeat_yet_is_not_a_claim_that_the_session_is_FINE():
    """`None` means "nothing has said". Reporting a degradation here would
    double-count a bridge that is merely starting up — `connected` is already
    False in that window."""
    listener = InstagramListener("http://x")

    assert listener.session_state is None
    assert listener.session_degraded is False


async def test_a_heartbeat_reporting_a_CHECKPOINT_degrades_the_listener(aiohttp_server, caplog):
    """The case startup cannot see: authenticated at boot, checkpointed at
    4am. Without this, `/health` would report a connected adapter in a room
    that has gone silent because every send is 409ing."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"heartbeat","ts_ms":1,"connected":true,"session":"checkpointed"}\n\n']
    ))
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with (
        caplog.at_level(logging.ERROR, logger="nora.instagram.listener"),
        pytest.raises(_Stop),
    ):
        await listener.listen(lambda e: asyncio.sleep(0))

    assert listener.session_state == "checkpointed"
    assert listener.session_degraded is True
    assert "checkpointed" in caplog.text
    assert "DEGRADED" in caplog.text


async def test_an_AUTHENTICATED_heartbeat_leaves_the_listener_undegraded(aiohttp_server):
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"heartbeat","ts_ms":1,"connected":true,"session":"authenticated"}\n\n']
    ))
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))

    assert listener.session_degraded is False


async def test_a_RECOVERED_session_stops_being_reported_as_degraded(aiohttp_server, caplog):
    """A checkpoint the operator clears must not need a restart to stop
    reading as a degradation — the runtime half tracks the live heartbeat."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"heartbeat","ts_ms":1,"session":"checkpointed"}\n\n',
         'data: {"kind":"heartbeat","ts_ms":2,"session":"authenticated"}\n\n']
    ))
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with (
        caplog.at_level(logging.INFO, logger="nora.instagram.listener"),
        pytest.raises(_Stop),
    ):
        await listener.listen(lambda e: asyncio.sleep(0))

    assert listener.session_degraded is False
    assert "recovered" in caplog.text


async def test_the_session_line_is_logged_on_the_TRANSITION_not_every_heartbeat(
    aiohttp_server, caplog,
):
    """A line every ~30s says nothing; a line on the change is what makes
    "she went deaf at 04:12" findable."""
    server = await aiohttp_server(await _sse_app(
        ['data: {"kind":"heartbeat","ts_ms":1,"session":"checkpointed"}\n\n'] * 4
    ))
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(1))
    with (
        caplog.at_level(logging.ERROR, logger="nora.instagram.listener"),
        pytest.raises(_Stop),
    ):
        await listener.listen(lambda e: asyncio.sleep(0))

    assert caplog.text.count("instagram session is now") == 1


async def test_reconnects_after_the_stream_closes(aiohttp_server):
    connections = {"n": 0}

    async def handler(request):
        connections["n"] += 1
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'data: {"kind":"heartbeat","ts_ms":1}\n\n')
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/events", handler)
    server = await aiohttp_server(app)
    listener = InstagramListener(str(server.make_url("")).rstrip("/"), sleeper=_stop_after(3))
    with pytest.raises(_Stop):
        await listener.listen(lambda e: asyncio.sleep(0))
    assert connections["n"] >= 2
    assert listener.connected is False


def test_backoff_is_exponential_capped_and_jittered():
    listener = InstagramListener("http://x", max_backoff=60.0)
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
