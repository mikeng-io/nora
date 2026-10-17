"""The `ReverseGeocoder` contract — one behavioural suite BOTH backends pass
(Google, HK-gov), plus `nora.testing.FakeReverseGeocoder` on the PORT-level
half. CLAUDE.md: a port never gains a second implementation — test fakes
included — without a shared contract suite both pass.

Two fixtures, on purpose. `adapter` (the two real backends) drives the shared
`CachingBilingualGeocoder` discipline — the «中文 / English» join and the ~11 m
memo — which is base-class behaviour a fake has no business re-implementing.
`geocoder` (backends AND the fake) drives what the PORT promises: a coordinate
answers a string, a failure answers None and never raises. This is where the
shared fake is held to the same shape as production.

The shared discipline (the bilingual «中文 / English» join, the ~11 m memo cache,
and never-raise fail-soft) lives ONCE in `CachingBilingualGeocoder`; this drives
it through each backend's REAL HTTP path against a fake server that answers in
that backend's own shape (Google's `{status, results:[{formatted_address}]}`, HK-
gov's Esri `{address:{LongLabel}}`), so a backend cannot answer the wrong shape
and still pass. Each adapter can `break_it()` into that backend's failure form
(Google's non-OK-in-200, HK-gov's error-object-in-200).
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import PlaceArea, ReverseGeocoder
from nora.infra import geocode
from nora.infra.geocode import GoogleGeocoder
from nora.infra.geocode_hkgov import HKGovGeocoder
from nora.testing import FakeGeocodeCacheStore, FakeReverseGeocoder


def _keep(area: PlaceArea) -> PlaceArea:
    """A screen that keeps every name (production passes
    `nora.app.gatherings.screened_area`)."""
    return area


class _GoogleAdapter:
    """`GoogleGeocoder` against a fake Google Geocoding host (language=zh-HK|en)."""

    async def start(self, monkeypatch) -> None:
        self.requests: list[str] = []
        self._ok = True

        async def handler(request: web.Request) -> web.Response:
            self.requests.append(request.query.get("language", ""))
            if not self._ok:
                return web.json_response({"status": "ZERO_RESULTS", "results": []})
            zh = request.query.get("language") == "zh-HK"
            return web.json_response({"status": "OK", "results": [{
                "formatted_address": "元朗形點" if zh else "Yoho Mall",
                "address_components": [
                    {"long_name": "元朗形點" if zh else "Yoho Mall", "types": ["premise"]},
                    {"long_name": "元朗" if zh else "Yuen Long",
                     "types": ["neighborhood", "political"]},
                    {"long_name": "新界" if zh else "New Territories",
                     "types": ["administrative_area_level_1", "political"]},
                ],
            }]})

        app = web.Application()
        app.router.add_get("/geo", handler)
        self._server = TestServer(app)
        await self._server.start_server()
        monkeypatch.setattr(geocode, "GEOCODE_URL", str(self._server.make_url("/geo")))
        self.geocoder = GoogleGeocoder(api_key="k")
        self.with_store = lambda store, screen=_keep: GoogleGeocoder(
            api_key="k", cache_store=store, area_screen=screen,
        )

    async def stop(self) -> None:
        await self._server.close()

    def break_it(self) -> None:
        self._ok = False


class _HKGovAdapter:
    """`HKGovGeocoder` against a fake Esri reverseGeocode host (langCode=zh|en)."""

    async def start(self, monkeypatch) -> None:
        self.requests: list[str] = []
        self._ok = True

        async def handler(request: web.Request) -> web.Response:
            self.requests.append(request.query.get("langCode", ""))
            if not self._ok:
                # ArcGIS reports failure as a 200 with an error object.
                return web.json_response({"error": {"code": 400, "message": "nope"}})
            zh = request.query.get("langCode") == "zh"
            return web.json_response({"address": {
                "LongLabel": "元朗形點" if zh else "Yoho Mall",
                "Neighborhood": "元朗" if zh else "Yuen Long",
                "City": "新界" if zh else "New Territories",
            }, "location": {}})

        app = web.Application()
        app.router.add_get("/rev", handler)
        self._server = TestServer(app)
        await self._server.start_server()
        url = str(self._server.make_url("/rev"))
        self.geocoder = HKGovGeocoder(service_url=url, token="t")  # noqa: S106
        self.with_store = lambda store, screen=_keep: HKGovGeocoder(
            service_url=url, token="t", cache_store=store, area_screen=screen,  # noqa: S106
        )

    async def stop(self) -> None:
        await self._server.close()

    def break_it(self) -> None:
        self._ok = False


class _FakeAdapter:
    """`FakeReverseGeocoder`, in the same harness shape as the two backends so
    the port-level tests below read identically across all three."""

    async def start(self, monkeypatch) -> None:
        self.geocoder = FakeReverseGeocoder("元朗形點 / Yoho Mall")

    async def stop(self) -> None:
        return None

    def break_it(self) -> None:
        self.geocoder.fail = True


_BACKENDS = {"google": _GoogleAdapter, "hkgov": _HKGovAdapter}


@pytest.fixture(params=["google", "hkgov"])
async def adapter(request, monkeypatch):
    """The two REAL backends — for the base-class behaviours (join, memo)."""
    a = _BACKENDS[request.param]()
    await a.start(monkeypatch)
    yield a
    await a.stop()


@pytest.fixture(params=["google", "hkgov", "fake"])
async def geocoder(request, monkeypatch):
    """Every implementation of the PORT, the shared fake included."""
    a = {**_BACKENDS, "fake": _FakeAdapter}[request.param]()
    await a.start(monkeypatch)
    yield a
    await a.stop()


def test_every_implementation_satisfies_the_port() -> None:
    for impl in (GoogleGeocoder(api_key="k"), HKGovGeocoder(service_url="http://x", token="t"),  # noqa: S106
                 FakeReverseGeocoder()):
        assert isinstance(impl, ReverseGeocoder), type(impl).__name__


async def test_a_coordinate_answers_a_place_name(geocoder):
    """The port's positive half, for every implementation: a string, reached
    keyword-only exactly as the collector calls it."""
    out = await geocoder.geocoder.address_for(lat=22.4445, lng=114.0289)
    assert isinstance(out, str) and out


async def test_a_coordinate_resolves_to_both_languages_joined(adapter):
    """Both backends return one language per call, and the base joins them
    «中文 / English» — Chinese first, for a Cantonese group."""
    assert await adapter.geocoder.address_for(lat=22.4445, lng=114.0289) == "元朗形點 / Yoho Mall"


async def test_a_stationary_coordinate_is_fetched_once_then_cached(adapter):
    """The ~11 m memo: two lookups within the rounding window → ONE zh+en pair of
    HTTP calls, not two. Re-billing a stationary doorstep is the cost it cuts."""
    a = await adapter.geocoder.address_for(lat=22.44451, lng=114.02890)
    b = await adapter.geocoder.address_for(lat=22.44453, lng=114.02889)   # same to 4 dp
    assert a == b == "元朗形點 / Yoho Mall"
    assert len(adapter.requests) == 2


async def test_the_areas_of_a_coordinate_are_stored_beside_its_address(adapter):
    """Both backends name the district and the city from their structured
    address fields, joined «中文 / English» like the address, and the store
    keeps them beside it: `nora.analytics` places an outing by them
    (design/unified-memory 00 step 3a)."""
    store = FakeGeocodeCacheStore()
    geo = adapter.with_store(store)
    assert await geo.address_for(lat=1.0, lng=2.0) == "元朗形點 / Yoho Mall"
    assert await store.area_within(lat=1.0, lng=2.0, radius_m=50.0) == PlaceArea(
        district="元朗 / Yuen Long", city="新界 / New Territories",
    )


async def test_the_areas_kept_are_what_the_screen_keeps_and_none_without_one(adapter):
    """This layer cannot import the R-28 screen, so it is handed in, and what
    the cache keeps is the screen's verdict: a name it drops is not stored.
    A geocoder built with no screen keeps no areas at all (fail closed); the
    address is cached either way."""
    store = FakeGeocodeCacheStore()
    assert await adapter.with_store(store, None).address_for(lat=1.0, lng=2.0)
    assert await store.area_within(lat=1.0, lng=2.0, radius_m=50.0) is None
    assert await adapter.with_store(store, None).area_for(lat=1.0, lng=2.0) is None

    def district_only(area: PlaceArea) -> PlaceArea:
        return PlaceArea(district=area.district)

    screened = adapter.with_store(store, district_only)
    assert await screened.area_for(lat=1.0, lng=3.0) == PlaceArea(district="元朗 / Yuen Long")
    assert await screened.address_for(lat=1.0, lng=3.0)
    assert await store.area_within(lat=1.0, lng=3.0, radius_m=50.0) == PlaceArea(
        district="元朗 / Yuen Long",
    )


async def test_a_failure_is_none_and_never_raises(geocoder):
    """Fail-soft by contract: each backend's own failure shape (Google's non-OK
    status, HK-gov's error object — both in a 200; the fake's `fail` switch)
    becomes None, never an exception in the collector loop and never a partial
    address."""
    geocoder.break_it()
    assert await geocoder.geocoder.address_for(lat=22.44, lng=114.02) is None
