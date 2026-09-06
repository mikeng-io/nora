"""Tests for the Honcho read path (peer_card, health) on nora/infra/honcho/client.py, spec §C.5.

Mirrors tests/test_honcho.py's (Task 21) local, zero-dependency server fixture: a
local aiohttp.test_utils.TestServer scoped to this file, no shared conftest changes
and no third-party pytest plugin registration.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient
from nora.testing import FakeMemoryStore


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
        _payload_handler({"peer_card": ["- likes espresso", "- lives in HK"]})
    )
    card = await HonchoClient(base_url, "nora").peer_card(peer_id="miles")
    assert card == ["likes espresso", "lives in HK"]


async def test_peer_card_returns_empty_on_a_server_error(server_factory):
    base_url = await server_factory(_payload_handler({}, status=500))
    assert await HonchoClient(base_url, "nora").peer_card(peer_id="miles") == []


async def test_peer_card_returns_empty_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", card_timeout=0.3)
    assert await client.peer_card(peer_id="miles") == []


async def test_peer_card_returns_empty_for_an_unparseable_body(server_factory):
    base_url = await server_factory(_payload_handler({"unexpected": True}))
    assert await HonchoClient(base_url, "nora").peer_card(peer_id="miles") == []


async def test_peer_card_uses_a_get_request_to_the_card_endpoint(server_factory):
    captured = {}

    async def handler(request: web.Request) -> web.Response:
        captured["method"] = request.method
        captured["path"] = request.path
        return web.json_response({"peer_card": ["- likes espresso"]})

    base_url = await server_factory(handler)

    await HonchoClient(base_url, "nora").peer_card(peer_id="miles")

    assert (captured["method"], captured["path"]) == ("GET", "/v3/workspaces/nora/peers/miles/card")


async def test_health_reports_reachability(server_factory):
    base_url = await server_factory(_payload_handler({"ok": True}))
    health = await HonchoClient(base_url, "nora").health()
    assert health.reachable is True


async def test_health_probes_the_root_health_endpoint(server_factory):
    """Regression guard (fix of 2026-08-04): the probe must be GET /health.

    GET /v3/workspaces is POST-only -> 405 -> the container sits in
    'health: starting' forever. The sibling health tests mount a wildcard route
    that answers any method on any path, so only this assertion catches it.
    """
    captured = {}

    async def handler(request):
        captured["method"] = request.method
        captured["path"] = request.path
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)

    await HonchoClient(base_url, "nora").health()

    assert (captured["method"], captured["path"]) == ("GET", "/health")


async def test_health_reports_unreachable_without_raising():
    health = await HonchoClient("http://127.0.0.1:1", "nora", card_timeout=0.3).health()
    assert health.reachable is False


async def test_fake_honcho_records_feeds():
    fake = FakeMemoryStore()
    await fake.feed(peer_id="miles", text="hello", conversation_id="c1")
    assert fake.fed == [("miles", "hello", "c1")]


async def test_fake_honcho_serves_configured_cards():
    fake = FakeMemoryStore(cards={"miles": ["likes espresso"]})
    assert await fake.peer_card(peer_id="miles") == ["likes espresso"]
    assert await fake.peer_card(peer_id="nobody") == []


async def test_fake_honcho_can_simulate_being_down():
    fake = FakeMemoryStore(fail=True)
    assert await fake.feed(peer_id="miles", text="x", conversation_id="c1") is False
    assert await fake.peer_card(peer_id="miles") == []


async def test_the_content_key_honcho_never_returns_yields_nothing(server_factory):
    """The shape this client invented and its old test asserted back to itself.

    `GET /peers/{id}/card` answers `{"peer_card": [...] | null}` — verified
    against the live instance and its OpenAPI schema. It has never returned a
    `content` string. Reading that key meant peer_card returned [] on every
    call and the tagging path's recall channel was dead for the life of the
    deployment, silently, because an empty card is indistinguishable from a
    person Nora simply has no memories about yet.

    Pinned so the invented shape cannot come back.
    """
    base_url = await server_factory(_payload_handler({"content": "- likes tea"}))
    assert await HonchoClient(base_url, "nora").peer_card(peer_id="miles") == []
