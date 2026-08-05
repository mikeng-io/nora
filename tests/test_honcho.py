"""Tests for the best-effort Honcho v3 write path (nora/infra/honcho/client.py, spec §C.5).

Every scenario below is offline: a local aiohttp.test_utils.TestServer stands in for
Honcho, so no live network is required (and none of these carry the `network` marker).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient


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

    assert await client.feed(peer_id="miles", text="hello") is True
    # The session is created at the WORKSPACE level. This test previously
    # asserted `/peers/miles/sessions`, which is a LIST endpoint — it answers
    # 200 with a pagination envelope carrying no `id`, so `ensure_session`
    # returned None, `feed` returned False before posting anything, and Honcho
    # received nothing for the entire life of the deployment. The test passed
    # throughout, because it was written against the implementation rather
    # than the API and its fake server answered whatever was asked of it.
    assert calls == [
        ("POST", "/v3/workspaces/nora/sessions"),
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

    assert await client.feed(peer_id="miles", text="hello") is True
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

    assert await client.feed(peer_id="miles", text="a") is True
    assert await client.feed(peer_id="miles", text="b") is True

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

    await client.ensure_peer(peer_id="miles")
    await client.ensure_peer(peer_id="miles")

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
    assert await client.feed(peer_id="miles", text="hello") is False


async def test_feed_never_raises_on_a_server_error(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": "boom"}, status=500)

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="hello") is False


async def test_feed_times_out_without_blocking_the_caller(server_factory):
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(5)
        return web.json_response({"id": "sess-1"})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora", feed_timeout=0.3)

    start = time.monotonic()
    result = await client.feed(peer_id="miles", text="hello")
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0


async def test_ensure_peer_never_raises_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", feed_timeout=0.2)
    await client.ensure_peer(peer_id="miles")  # must not raise


async def test_ensure_peer_is_idempotent_under_concurrency(monkeypatch):
    """Two concurrent misses must create ONE peer, not two. Person-centric
    identity means Miles's Signal and Telegram messages hit the same peer id at
    the same moment, and a split peer splits his card."""
    client = HonchoClient("http://127.0.0.1:8000", "nora")
    creates = []

    async def _fake_create(peer_id):
        await asyncio.sleep(0.01)      # widen the check-then-act window
        creates.append(peer_id)

    async def _fake_exists(peer_id):
        return peer_id in creates

    monkeypatch.setattr(client, "_peer_exists", _fake_exists)
    monkeypatch.setattr(client, "_create_peer", _fake_create)

    await asyncio.gather(*(client.ensure_peer(peer_id="miles") for _ in range(5)))
    assert creates == ["miles"]


async def test_different_peers_are_not_serialised_behind_one_lock(monkeypatch):
    """A single global lock would make this correct but slow; the lock is
    per-peer, so two different peers proceed concurrently."""
    client = HonchoClient("http://127.0.0.1:8000", "nora")
    assert client._peer_lock("a") is not client._peer_lock("b")
    assert client._peer_lock("a") is client._peer_lock("a")


async def test_the_session_is_created_at_the_workspace_level(server_factory):
    """The bug that made Honcho a no-op for the deployment's entire life.

    `POST /peers/{id}/sessions` is a LIST endpoint on the real server: it
    answers **200** with a pagination envelope and no `id`. `ensure_session`
    read `.get("id")` off that, got None, and `feed` returned False before
    posting anything — silently, because feeding is best-effort by contract
    and the caller only counts a failure. Both feed counters sat at 0.0 and
    nothing else noticed.

    The old test could not have caught it: its fake answered every path with a
    created session, so it asserted the implementation back to itself. This
    fake answers the way the LIVE server does — verified against it — which is
    what makes the URL load-bearing here.
    """
    calls: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append(request.path)
        if request.path.endswith("/peers/miles/sessions"):
            # what the real server returns: a list, not a creation
            return web.json_response({"items": [], "total": 0, "page": 1, "pages": 0})
        if request.path.endswith("/v3/workspaces/nora/sessions"):
            return web.json_response({"id": "nora-miles"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="hello") is True
    assert "/v3/workspaces/nora/sessions" in calls
    assert "/v3/workspaces/nora/sessions/nora-miles/messages" in calls


async def test_a_list_shaped_session_response_is_not_mistaken_for_a_created_one(

    server_factory,
):
    """The bug that made Honcho a no-op for the deployment's entire life.

    `POST /peers/{id}/sessions` is a LIST endpoint: it answers **200** with a
    pagination envelope and no `id`. `ensure_session` read `.get("id")` off it,
    got None, and `feed` returned False before posting anything — silently,
    because feeding is best-effort by contract and the caller just counts a
    failure. Both feed counters sat at 0.0 and nothing else noticed.

    A 200 that is not a created session must NOT be treated as one.
    """
    calls: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append(request.path)
        return web.json_response({"items": [], "total": 0, "page": 1, "pages": 0})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.ensure_session("miles") is None
    assert await client.feed(peer_id="miles", text="hello") is False
    assert not any("/messages" in c for c in calls), (
        "no message may be posted when the session was never created"
    )
