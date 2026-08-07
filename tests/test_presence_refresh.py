"""`HttpLocationRefresher` — the where_is→collector /refresh call (DEC-67). A 200
is a fresh True (and the person_id rides the query); anything else — non-200,
unreachable — is False and NEVER raises, so where_is falls back to the stored fix.
"""

from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.presence_refresh import HttpLocationRefresher


async def _server(handler):
    app = web.Application()
    app.router.add_post("/refresh", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_a_200_is_fresh_true_and_carries_the_person_id():
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen["pid"] = request.query.get("person_id", "")
        return web.json_response({"status": "fresh"})

    server = await _server(handler)
    try:
        refresher = HttpLocationRefresher(base_url=str(server.make_url("/")), timeout_seconds=2.0)
        assert await refresher.refresh(person_id="miles_novak") is True
        assert seen["pid"] == "miles_novak"
    finally:
        await server.close()


async def test_a_non_200_is_false():
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"status": "stale"}, status=408)   # timed out / nothing fresher

    server = await _server(handler)
    try:
        refresher = HttpLocationRefresher(base_url=str(server.make_url("/")), timeout_seconds=2.0)
        assert await refresher.refresh(person_id="miles_novak") is False
    finally:
        await server.close()


async def test_an_unreachable_collector_is_false_and_never_raises():
    refresher = HttpLocationRefresher(base_url="http://127.0.0.1:1", timeout_seconds=0.3)
    assert await refresher.refresh(person_id="miles_novak") is False
