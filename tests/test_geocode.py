"""`ReverseGeocoder` — coordinate → place, and its fail-soft / memo discipline. Runs offline
against a local aiohttp server that behaves like the Google Geocoding API, INCLUDING the
shapes that make it hard: a non-OK `status` arrives as a 200, and the key rides in the
query string.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import PlaceArea
from nora.infra import geocode
from nora.infra.geocode import GoogleGeocoder
from nora.testing import FakeGeocodeCacheStore


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
        zh = request.query.get("language") == "zh-HK"
        if request.query.get("place_id"):
            # The place-id lookup a venue's areas come from: a result whose
            # components name a neighbourhood and Hong Kong's first-level area
            # (no `locality`), plus a country that is neither.
            if request.query["place_id"] != "ChIJ-venue":
                return web.json_response({"status": "ZERO_RESULTS", "results": []})
            return web.json_response({"status": "OK", "results": [{
                "formatted_address": "venue",
                "address_components": [
                    {"long_name": "8號" if zh else "8", "types": ["street_number"]},
                    {"long_name": "旺角" if zh else "Mong Kok",
                     "types": ["neighborhood", "political"]},
                    {"long_name": "九龍" if zh else "Kowloon",
                     "types": ["administrative_area_level_1", "political"]},
                    {"long_name": "香港" if zh else "Hong Kong",
                     "types": ["country", "political"]},
                ],
            }]})
        addr = "元朗形點" if zh else "Yoho Mall, Yuen Long"
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
    addr = await GoogleGeocoder(api_key="k").address_for(lat=22.4445, lng=114.0289)
    assert addr == "元朗形點 / Yoho Mall, Yuen Long"
    assert google.requests == ["22.4445,114.0289", "22.4445,114.0289"]


async def test_a_non_ok_status_is_no_address_not_a_partial(google):
    google.mode = "denied"
    assert await GoogleGeocoder(api_key="k").address_for(lat=22.44, lng=114.02) is None


async def test_zero_results_is_none(google):
    google.mode = "zero"
    assert await GoogleGeocoder(api_key="k").address_for(lat=22.44, lng=114.02) is None


async def test_a_non_json_body_is_none(google):
    google.mode = "notjson"
    assert await GoogleGeocoder(api_key="k").address_for(lat=22.44, lng=114.02) is None


async def test_the_same_place_is_geocoded_once(google):
    """A stationary member reports a near-identical coordinate every cycle; the
    memo-cache (rounded to ~11 m) must not re-bill Google for the same doorstep.
    Two lookups within the rounding window → ONE pair of HTTP calls (zh + en),
    not two — the second `address_for` is served from the cache."""
    geo = GoogleGeocoder(api_key="k")
    a = await geo.address_for(lat=22.44451, lng=114.02890)
    b = await geo.address_for(lat=22.44453, lng=114.02889)   # same to 4 dp
    assert a == b == "元朗形點 / Yoho Mall, Yuen Long"
    assert len(google.requests) == 2                 # one zh+en pair, then cached


async def test_a_miss_is_not_cached(google):
    """A transient outage returns None and must NOT pin the coordinate to "no
    address" — the next cycle retries. A ZERO then an OK for the same spot makes
    two PAIRS of calls (zh+en each) and eventually resolves."""
    google.mode = "zero"
    geo = GoogleGeocoder(api_key="k")
    assert await geo.address_for(lat=22.44, lng=114.02) is None
    google.mode = "ok"
    assert await geo.address_for(lat=22.44, lng=114.02) == "元朗形點 / Yoho Mall, Yuen Long"
    assert len(google.requests) == 4


async def test_a_dead_host_is_none_never_raises(monkeypatch):
    monkeypatch.setattr(geocode, "GEOCODE_URL", "http://127.0.0.1:1/dead")
    geo = GoogleGeocoder(api_key="k", timeout_seconds=0.5)
    assert await geo.address_for(lat=1.0, lng=2.0) is None


async def test_a_persisted_hit_is_served_without_calling_google(google):
    """The whole point of the persistent store: a coordinate resolved in an
    EARLIER process (a restart-recovered hit) must never re-bill Google."""
    store = FakeGeocodeCacheStore()
    await store.put(lat_rounded=22.4445, lng_rounded=114.0289, address="cached spot")
    geo = GoogleGeocoder(api_key="k", cache_store=store)
    addr = await geo.address_for(lat=22.44451, lng=114.02889)  # rounds to the same key
    assert addr == "cached spot"
    assert google.requests == []


async def test_a_fresh_resolve_is_persisted_to_the_store(google):
    store = FakeGeocodeCacheStore()
    geo = GoogleGeocoder(api_key="k", cache_store=store)
    addr = await geo.address_for(lat=22.4445, lng=114.0289)
    assert addr == "元朗形點 / Yoho Mall, Yuen Long"
    assert store.puts == [(22.4445, 114.0289, "元朗形點 / Yoho Mall, Yuen Long")]


async def test_a_geocode_miss_is_not_persisted(google):
    google.mode = "zero"
    store = FakeGeocodeCacheStore()
    geo = GoogleGeocoder(api_key="k", cache_store=store)
    assert await geo.address_for(lat=22.44, lng=114.02) is None
    assert store.puts == []


async def test_a_store_read_failure_still_resolves_live(google):
    """`GeocodeCacheStore` never raises by contract, but a caller must not
    assume that — belt and braces, matching this module's own fail-soft
    posture for every other collaborator."""

    class _RaisingStore:
        async def get(self, *, lat_rounded: float, lng_rounded: float) -> str | None:
            raise RuntimeError("db unavailable")

        async def put(self, **kw: object) -> None:
            raise RuntimeError("db unavailable")

    geo = GoogleGeocoder(api_key="k", cache_store=_RaisingStore())
    addr = await geo.address_for(lat=22.4445, lng=114.0289)
    assert addr == "元朗形點 / Yoho Mall, Yuen Long"


async def test_no_store_behaves_exactly_as_before(google):
    """`cache_store` is optional — a caller that does not wire one gets in-memory-only
    behaviour."""
    geo = GoogleGeocoder(api_key="k")
    assert await geo.address_for(lat=22.4445, lng=114.0289) == (
        "元朗形點 / Yoho Mall, Yuen Long"
    )


# ---- Plus Code screening --------------------------------------------------


def test_a_plus_code_embedded_after_cjk_is_rejected():
    """Google's zh result embeds the Plus Code directly after CJK (香港新界元朗FQQQ+QQ) — a
    word-boundary regex does not fire there, so the screen uses a boundary-free pattern."""
    payload = {
        "status": "OK",
        "results": [
            {"formatted_address": "香港新界元朗FQQQ+QQ",
             "types": ["premise", "street_address"]},
            {"formatted_address": "香港新界元朗南生圍路", "types": ["route"]},
        ],
    }
    assert geocode._first_address(payload) == "香港新界元朗南生圍路"


def test_a_leading_plus_code_is_rejected():
    payload = {
        "status": "OK",
        "results": [
            {"formatted_address": "FQQQ+QQ, Yuen Long, New Territories, Hong Kong",
             "types": ["premise", "street_address"]},
            {"formatted_address": "Nam Sang Chi Rd, Yuen Long, New Territories, Hong Kong",
             "types": ["route"]},
        ],
    }
    expected = (
        "Nam Sang Chi Rd, Yuen Long, New Territories, Hong Kong"
    )
    assert geocode._first_address(payload) == expected


def test_a_pure_plus_code_typed_result_is_rejected():
    payload = {
        "status": "OK",
        "results": [
            {"formatted_address": "7QQQ+QQ 香港大屿山", "types": ["plus_code"]},
            {"formatted_address": "香港石仔埗街", "types": ["route"]},
        ],
    }
    assert geocode._first_address(payload) == "香港石仔埗街"


def test_all_plus_code_results_answer_none_never_a_grid_reference():
    """Open sea: every result is a Plus Code. None — the caller renders
    not_found — never a grid reference dressed as an address."""
    payload = {
        "status": "OK",
        "results": [
            {"formatted_address": "7QQQQQQQ+QQ", "types": ["plus_code"]},
        ],
    }
    assert geocode._first_address(payload) is None


def test_real_addresses_are_never_rejected_by_the_plus_code_screen():
    payload = {
        "status": "OK",
        "results": [
            {"formatted_address": "香港新界天水圍天瑞路77號",
             "types": ["establishment", "point_of_interest"]},
        ],
    }
    assert geocode._first_address(payload) == "香港新界天水圍天瑞路77號"


async def test_a_venue_place_id_answers_its_district_and_city_in_both_languages(google):
    """`area_of_place` asks the Geocoding API by Google's own place id (no
    coordinate goes out), once per language, and names the neighbourhood and
    what holds it from the components, never the street or the country."""
    geo = GoogleGeocoder(api_key="k")
    assert await geo.area_of_place("ChIJ-venue") == PlaceArea(
        district="旺角 / Mong Kok", city="九龍 / Kowloon",
    )
    assert await geo.area_of_place("ChIJ-unknown") is None
    google.mode = "denied"
    assert await geo.area_of_place("ChIJ-venue") is None
    assert await geo.area_of_place("") is None
