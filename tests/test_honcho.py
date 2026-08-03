"""Tests for the best-effort Honcho v3 write path (nora/store/honcho.py, spec §C.5).

Every scenario below is offline: a local aiohttp.test_utils.TestServer stands in for
Honcho, so no live network is required (and none of these carry the `network` marker).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.store.honcho import HonchoClient


@pytest.fixture
async def server_factory():
    """Yield a factory that spins up a local aiohttp server for one test's handler."""
    servers: list[TestServer] = []

    async def _make(handler) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("/")).rstrip("/")

    yield _make

    for server in servers:
        await server.close()


async def test_feed_creates_a_session_then_posts_the_message_batch(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed("miles", "hello") is True
    assert calls == [
        ("POST", "/v3/workspaces/nora/peers/miles/sessions"),
        ("POST", "/v3/workspaces/nora/sessions/sess-1/messages"),
    ]


async def test_feed_sends_the_correct_message_batch_body(server_factory):
    bodies: dict[str, object] = {}

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if request.path.endswith("/messages"):
            bodies["messages"] = body
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed("miles", "hello") is True
    assert bodies["messages"] == {"messages": [{"content": "hello", "peer_id": "miles"}]}


async def test_feed_reuses_the_cached_session_for_the_same_peer(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed("miles", "a") is True
    assert await client.feed("miles", "b") is True

    session_calls = [c for c in calls if c[1].endswith("/sessions")]
    message_calls = [c for c in calls if c[1].endswith("/messages")]
    assert len(session_calls) == 1
    assert len(message_calls) == 2


async def test_ensure_peer_is_idempotent(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    await client.ensure_peer("miles")
    await client.ensure_peer("miles")

    # Careful not to over-match /peers/miles/sessions — filter for paths ending exactly
    # in "/peers".
    peer_calls = [c for c in calls if c[1].endswith("/peers")]
    assert len(peer_calls) == 1


async def test_ensure_workspace_is_called_once(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    await client.ensure_workspace()
    await client.ensure_workspace()

    ws_calls = [c for c in calls if c[1] == "/v3/workspaces"]
    assert len(ws_calls) == 1


async def test_feed_returns_false_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", feed_timeout=0.3)
    assert await client.feed("miles", "hello") is False


async def test_feed_never_raises_on_a_server_error(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": "boom"}, status=500)

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed("miles", "hello") is False


async def test_feed_times_out_without_blocking_the_caller(server_factory):
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(5)
        return web.json_response({"id": "sess-1"})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora", feed_timeout=0.3)

    start = time.monotonic()
    result = await client.feed("miles", "hello")
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0


async def test_ensure_peer_never_raises_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", feed_timeout=0.2)
    await client.ensure_peer("miles")  # must not raise
