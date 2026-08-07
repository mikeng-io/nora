"""`ReverseGeocoder` — coordinate → place, and its fail-soft / memo discipline
(design/19 §5). Runs offline against a local aiohttp server that behaves like the
Google Geocoding API, INCLUDING the shapes that make it hard: a non-OK `status`
arrives as a 200, and the key rides in the query string.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra import geocode
from nora.infra.geocode import ReverseGeocoder


class _Google:
    """The Geocoding endpoint, behaving like the real host: `status` in a 200
    body, an `OK` result, and a request log so the memo-cache can be proven."""

    def __init__(self) -> None:
        self.mode = "ok"          # ok | zero | denied | notjson
        self.requests: list[str] = []

    async def geocode(self, request: web.Request) -> web.Response:
        self.requests.append(request.query.get("latlng", ""))
        if self.mode == "notjson":
            return web.Response(text="<html>nope</html>", content_type="text/html")
        if self.mode == "zero":
            return web.json_response({"status": "ZERO_RESULTS", "results": []})
        if self.mode == "denied":
            # A non-OK status still arrives as a 200 — must be read as "no
            # address", never as a partial result.
            return web.json_response({"status": "REQUEST_DENIED", "results": []})
        # Language-specific, like the real host: `address_for` asks for zh-HK AND
        # en and joins them, so the fake answers each call in the requested
        # language to prove the join.
        addr = "元朗形點" if request.query.get("language") == "zh-HK" else "Yoho Mall, Yuen Long"
        return web.json_response(
            {"status": "OK", "results": [{"formatted_address": addr}]}
        )


@pytest.fixture
async def google(monkeypatch):
    fake = _Google()
    app = web.Application()
    app.router.add_get("/maps/api/geocode/json", fake.geocode)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(geocode, "GEOCODE_URL", str(server.make_url("/maps/api/geocode/json")))
    yield fake
    await server.close()


async def test_a_coordinate_resolves_to_both_languages_joined(google):
    """The group reads both, and Life360's label was bilingual, so `address_for`
    asks zh-HK AND en and joins «中文 / English» (Chinese first). Two HTTP calls for
    one coordinate."""
    addr = await ReverseGeocoder(api_key="k").address_for(22.4445, 114.0289)
    assert addr == "元朗形點 / Yoho Mall, Yuen Long"
    assert google.requests == ["22.4445,114.0289", "22.4445,114.0289"]


async def test_a_non_ok_status_is_no_address_not_a_partial(google):
    google.mode = "denied"
    assert await ReverseGeocoder(api_key="k").address_for(22.44, 114.02) is None


async def test_zero_results_is_none(google):
    google.mode = "zero"
    assert await ReverseGeocoder(api_key="k").address_for(22.44, 114.02) is None


async def test_a_non_json_body_is_none(google):
    google.mode = "notjson"
    assert await ReverseGeocoder(api_key="k").address_for(22.44, 114.02) is None


async def test_the_same_place_is_geocoded_once(google):
    """A stationary member reports a near-identical coordinate every cycle; the
    memo-cache (rounded to ~11 m) must not re-bill Google for the same doorstep.
    Two lookups within the rounding window → ONE pair of HTTP calls (zh + en),
    not two — the second `address_for` is served from the cache."""
    geo = ReverseGeocoder(api_key="k")
    a = await geo.address_for(22.44451, 114.02890)
    b = await geo.address_for(22.44453, 114.02889)   # same to 4 dp
    assert a == b == "元朗形點 / Yoho Mall, Yuen Long"
    assert len(google.requests) == 2                 # one zh+en pair, then cached


async def test_a_miss_is_not_cached(google):
    """A transient outage returns None and must NOT pin the coordinate to "no
    address" — the next cycle retries. A ZERO then an OK for the same spot makes
    two PAIRS of calls (zh+en each) and eventually resolves."""
    google.mode = "zero"
    geo = ReverseGeocoder(api_key="k")
    assert await geo.address_for(22.44, 114.02) is None
    google.mode = "ok"
    assert await geo.address_for(22.44, 114.02) == "元朗形點 / Yoho Mall, Yuen Long"
    assert len(google.requests) == 4


async def test_a_dead_host_is_none_never_raises(monkeypatch):
    monkeypatch.setattr(geocode, "GEOCODE_URL", "http://127.0.0.1:1/dead")
    assert await ReverseGeocoder(api_key="k", timeout_seconds=0.5).address_for(1.0, 2.0) is None
