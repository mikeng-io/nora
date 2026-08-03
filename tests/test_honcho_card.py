import pytest
from aiohttp import web

from nora.store.honcho import HonchoClient
from tests.fakes import FakeHoncho


async def _card_server(aiohttp_server, payload, status=200):
    async def handler(request):
        if status != 200:
            return web.Response(status=status)
        return web.json_response(payload)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return await aiohttp_server(app)


async def test_peer_card_returns_a_list_of_facts(aiohttp_server):
    s = await _card_server(aiohttp_server, {"content": "- likes espresso\n- lives in HK"})
    card = await HonchoClient(str(s.make_url("")).rstrip("/"), "nora").peer_card("miles")
    assert card == ["likes espresso", "lives in HK"]


async def test_peer_card_returns_empty_on_a_server_error(aiohttp_server):
    s = await _card_server(aiohttp_server, {}, status=500)
    assert await HonchoClient(str(s.make_url("")).rstrip("/"), "nora").peer_card("miles") == []


async def test_peer_card_returns_empty_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", card_timeout=0.3)
    assert await client.peer_card("miles") == []


async def test_peer_card_returns_empty_for_an_unparseable_body(aiohttp_server):
    s = await _card_server(aiohttp_server, {"unexpected": True})
    assert await HonchoClient(str(s.make_url("")).rstrip("/"), "nora").peer_card("miles") == []


async def test_peer_card_uses_a_get_request_to_the_card_endpoint(aiohttp_server):
    captured = {}

    async def handler(request):
        captured["method"] = request.method
        captured["path"] = request.path
        return web.json_response({"content": "- likes espresso"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    s = await aiohttp_server(app)

    await HonchoClient(str(s.make_url("")).rstrip("/"), "nora").peer_card("miles")

    assert (captured["method"], captured["path"]) == ("GET", "/v3/workspaces/nora/peers/miles/card")


async def test_health_reports_reachability(aiohttp_server):
    s = await _card_server(aiohttp_server, {"ok": True})
    health = await HonchoClient(str(s.make_url("")).rstrip("/"), "nora").health()
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
