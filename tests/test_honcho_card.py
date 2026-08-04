"""Tests for the Honcho read path (peer_card, health) on nora/store/honcho.py, spec §C.5.

Mirrors tests/test_honcho.py's (Task 21) local, zero-dependency server fixture: a
local aiohttp.test_utils.TestServer scoped to this file, no shared conftest changes
and no third-party pytest plugin registration.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.store.honcho import HonchoClient
from tests.fakes import FakeHoncho


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


def _payload_handler(payload, status=200):
    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.Response(status=status)
        return web.json_response(payload)

    return handler


async def test_peer_card_returns_a_list_of_facts(server_factory):
    base_url = await server_factory(
        _payload_handler({"content": "- likes espresso\n- lives in HK"})
    )
    card = await HonchoClient(base_url, "nora").peer_card("miles")
    assert card == ["likes espresso", "lives in HK"]


async def test_peer_card_returns_empty_on_a_server_error(server_factory):
    base_url = await server_factory(_payload_handler({}, status=500))
    assert await HonchoClient(base_url, "nora").peer_card("miles") == []


async def test_peer_card_returns_empty_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", card_timeout=0.3)
    assert await client.peer_card("miles") == []


async def test_peer_card_returns_empty_for_an_unparseable_body(server_factory):
    base_url = await server_factory(_payload_handler({"unexpected": True}))
    assert await HonchoClient(base_url, "nora").peer_card("miles") == []


async def test_peer_card_uses_a_get_request_to_the_card_endpoint(server_factory):
    captured = {}

    async def handler(request: web.Request) -> web.Response:
        captured["method"] = request.method
        captured["path"] = request.path
        return web.json_response({"content": "- likes espresso"})

    base_url = await server_factory(handler)

    await HonchoClient(base_url, "nora").peer_card("miles")

    assert (captured["method"], captured["path"]) == ("GET", "/v3/workspaces/nora/peers/miles/card")


async def test_health_reports_reachability(server_factory):
    base_url = await server_factory(_payload_handler({"ok": True}))
    health = await HonchoClient(base_url, "nora").health()
    assert health["reachable"] is True


async def test_health_reports_unreachable_without_raising():
    health = await HonchoClient("http://127.0.0.1:1", "nora", card_timeout=0.3).health()
    assert health["reachable"] is False


async def test_fake_honcho_records_feeds():
    fake = FakeHoncho()
    await fake.feed("miles", "hello")
    assert fake.fed == [("miles", "hello")]


async def test_fake_honcho_serves_configured_cards():
    fake = FakeHoncho(cards={"miles": ["likes espresso"]})
    assert await fake.peer_card("miles") == ["likes espresso"]
    assert await fake.peer_card("nobody") == []


async def test_fake_honcho_can_simulate_being_down():
    fake = FakeHoncho(fail=True)
    assert await fake.feed("miles", "x") is False
    assert await fake.peer_card("miles") == []
