"""Reverse geocode one coordinate to a place name — or None. NEVER raises.

Life360 attaches a coarse `address1` to most fixes, so this is the FALLBACK for
the ones it does not: a fix Nora would otherwise have to report as
bare «22.31, 114.16», turned into «Yoho Mall» so she can answer «where are they»
with a place.

It lives here, at the infra level and NOT under `nora.infra.routes`, on purpose:
the caller is the presence COLLECTOR, and coupling the collector to Nora's tool
infra would drag the whole routing package into a second process for one call.
The collector is also the RIGHT place for it: this IS a coordinate egress — the
collector sends the member's coordinate to Google's (or the HK-gov) geocoding API
to turn it into a place name. That egress is the collector's alone and never the
model's: the resolved NAME is what reaches Nora, the coordinate never does
(DEC-24/60), and `whereis` stays a pure local-DB read that itself sends nothing.
The call is off-host to Google (DEC-69). R-28 governs what leaves toward the
MODEL/GROUP, and the collector is neither, so sending the coordinate host→Google
to get a name back is an accepted egress, not an R-28 breach.

Uses the classic Geocoding API, which is a GET with the key in the query string
(NOT the `X-Goog-Api-Key` header the Routes/Places v1 endpoints use — a different
Google product, a different auth shape). Like every outbound helper in this
codebase it degrades to None on any failure: an address is a nice-to-have, never
load-bearing, so a geocode outage must cost a coordinate-only fix, never a raised
exception in the collector loop or a stalled cycle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

from nora.domain.ports import PlaceArea, join_bilingual

if TYPE_CHECKING:
    from nora.domain.ports import GeocodeCacheStore

log = logging.getLogger("nora.geocode")

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# A Plus Code (Open Location Code) appearing INSIDE a formatted address -
# e.g. 香港新界元朗FQQQ+QQ or FQQQ+QQ, Yuen Long. No word boundary: the
# backslash-b form fails at the CJK-alphanumeric edge, which is exactly
# where Google embeds them for coordinates with no street address. Such an
# address is grid machinery, not something a
# human reads aloud, so the selector treats it as a miss.
_PLUS_CODE_IN_ADDRESS = re.compile(r"[A-Z0-9]{4}\+[A-Z0-9]{2,3}", re.IGNORECASE)

# Google's address-component types for the two areas a place lies in, most
# specific first: the neighbourhood (旺角 / Mong Kok) and what holds it (九龍 /
# Kowloon; Hong Kong has no `locality`, so its first-level area stands in).
_DISTRICT_TYPES = ("neighborhood", "sublocality_level_1", "sublocality")
_CITY_TYPES = ("locality", "administrative_area_level_1")


@dataclass(frozen=True)
class Geocoded:
    """One backend's answer in one language: the formatted address and the
    areas its structured address components name (None where it names none)."""

    address: str
    district: str | None = None
    city: str | None = None


def joined(zh: Geocoded | None, en: Geocoded | None) -> tuple[str, PlaceArea] | None:
    """The «中文 / English» address and areas of one coordinate's two answers;
    None when neither language answered."""
    address = join_bilingual(zh.address if zh else None, en.address if en else None)
    if address is None:
        return None
    area = PlaceArea(
        district=join_bilingual(zh.district if zh else None, en.district if en else None),
        city=join_bilingual(zh.city if zh else None, en.city if en else None),
    )
    return address, area


class CachingBilingualGeocoder:
    """The shared discipline every `ReverseGeocoder` implementation (Google today,
    HK-gov as a switchable option) inherits: a ~11 m memo cache, the bilingual
    «中文 / English» join, and fail-soft.

    A subclass implements ONLY `_fetch(lat, lng, language)` — one reverse-geocode
    in one language (`"zh"` | `"en"`), returning the formatted address or None on
    ANY failure. The caching, the two-language join, and the never-raise policy
    are written and TESTED once, here, so a second backend cannot drift from it
    (the `tests/contract/` suite pins this shape for every impl).
    """

    # Round to ~11 m (4 dp) for the memo key: a stationary member reports a
    # jittering-but-nearly-identical coordinate every cycle, and re-billing a
    # geocode for the same doorstep every 90 s is the cost this cache exists to
    # cut. Bounded so a mover who visits many places cannot grow it without limit.
    _CACHE_PRECISION = 4
    _CACHE_MAX = 512

    def __init__(self, *, cache_store: GeocodeCacheStore | None = None) -> None:
        # (rounded lat, rounded lng) -> address. Only successful lookups are
        # cached; a miss (None) is NOT stored, so a transient geocode outage does
        # not pin a coordinate to "no address" for the life of the process.
        self._cache: dict[tuple[float, float], str] = {}
        # The durable half: survives a restart, where the dict above does not.
        # Optional — `where_is` wires it; without it the cache is in-memory only.
        self._store = cache_store

    async def address_for(self, *, lat: float, lng: float) -> str | None:
        """The best formatted address for `lat,lng` in BOTH languages, or None.
        NEVER raises.

        The group reads both, and Life360's own label was bilingual («雲杉徑
        Cloudfir Path»), so store both: a backend returns ONE language per
        call, so this asks for `zh` and `en` and joins them «中文 / English» (Chinese first —
        a Cantonese group). One language missing still yields the other; both
        missing is None (the caller falls back to Life360's label, or the raw
        coordinate). Two calls per NEW coordinate, but the ~11 m cache means a
        stationary member pays it once, not every cycle — and with `_store`
        wired, once per COORDINATE ever, not once per process lifetime.

        The same two answers' areas (`PlaceArea`, joined the same way) are
        stored beside the address in `_store`, where `nora.analytics` reads
        them to place an outing; this method returns the address only.
        """
        key = (round(lat, self._CACHE_PRECISION), round(lng, self._CACHE_PRECISION))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self._store is not None:
            try:
                persisted = await self._store.get(lat_rounded=key[0], lng_rounded=key[1])
            except Exception:
                # The port promises never to raise; a caller must not depend
                # on that — a store outage costs one live re-resolve, same as
                # a cold cache, never a raised exception here.
                log.exception("geocode_cache read failed — resolving live instead")
                persisted = None
            if persisted is not None:
                self._remember(key, persisted)
                return persisted
        both = joined(await self._fetch(lat, lng, "zh"), await self._fetch(lat, lng, "en"))
        if both is None:
            return None
        address, area = both
        self._remember(key, address)
        if self._store is not None:
            try:
                await self._store.put(
                    lat_rounded=key[0], lng_rounded=key[1], address=address, area=area,
                )
            except Exception:
                log.exception("geocode_cache write failed; the address is not cached")
        return address

    def _remember(self, key: tuple[float, float], address: str) -> None:
        if len(self._cache) >= self._CACHE_MAX:
            # Drop the oldest entry (dicts preserve insertion order) so a
            # long-running process's in-memory tier stays bounded.
            del self._cache[next(iter(self._cache))]
        self._cache[key] = address

    async def _fetch(self, lat: float, lng: float, language: str) -> Geocoded | None:
        raise NotImplementedError


class GoogleGeocoder(CachingBilingualGeocoder):
    """`lat, lng -> formatted address` against the Google Geocoding API.

    Constructed with the resolved key (DEC-8: from the environment at the
    composition root, never read here). Built only when a key is present; a
    collector with no key simply has no geocoder and stores the coordinate as-is.
    """

    def __init__(
        self, *, api_key: str, timeout_seconds: float = 5.0,
        cache_store: GeocodeCacheStore | None = None,
    ) -> None:
        super().__init__(cache_store=cache_store)
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def _fetch(self, lat: float, lng: float, language: str) -> Geocoded | None:
        """One Google reverse-geocode in `language` (`"zh"` → Google's `zh-HK`).
        None on any failure — a non-200, a non-OK envelope status, non-JSON, or a
        transport error — so a missing address is never a dropped cycle."""
        # `result_type` is Google's own reverse-geocode filter (Geocoding API
        # request docs): it drops the pure `plus_code`-typed results. The
        # embedded-in-a-premise form still needs the pattern screen in
        # `_first_result` — the two layers cover each other's gaps.
        payload = await self._get({
            "latlng": f"{lat},{lng}",
            "result_type": "street_address|route|premise|establishment",
        }, language)
        result = _first_result(payload)
        if result is None:
            return None
        district, city = _area_of(result)
        return Geocoded(
            str(result.get("formatted_address") or "").strip(), district=district, city=city,
        )

    async def area_of_place(self, place_id: str) -> PlaceArea | None:
        """The areas of the place Google knows by `place_id` (a venue
        `place_info` resolved), from the Geocoding API's place-id lookup in
        both languages, joined «中文 / English». NEVER raises; None when
        neither answer names an area. No coordinate goes out: the id is
        Google's own."""
        if not place_id:
            return None
        answers = []
        for language in ("zh", "en"):
            payload = await self._get({"place_id": place_id}, language)
            results = payload.get("results") if isinstance(payload, dict) else None
            ok = isinstance(payload, dict) and payload.get("status") == "OK"
            first = results[0] if ok and isinstance(results, list) and results else None
            answers.append(_area_of(first) if isinstance(first, dict) else (None, None))
        (zh_d, zh_c), (en_d, en_c) = answers
        area = PlaceArea(district=join_bilingual(zh_d, en_d), city=join_bilingual(zh_c, en_c))
        return area or None

    async def _get(self, params: dict[str, str], language: str) -> Any:
        """One Geocoding API GET in `language`; the decoded body, or None on
        any failure."""
        params = {
            **params, "key": self._api_key, "language": "zh-HK" if language == "zh" else "en",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(GEOCODE_URL, params=params) as response,
            ):
                if response.status != 200:
                    # The key rides in the query; never log the URL or params.
                    log.warning("geocode answered %s", response.status)
                    return None
                try:
                    return await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    log.warning("geocode did not return JSON")
                    return None
        except Exception as exc:  # noqa: BLE001
            # Broad and never re-raised: this runs inside the collector loop, and
            # aiohttp raises ClientError, TimeoutError and SSLError from here. An
            # address is never load-bearing, so any failure is a missing address,
            # not a dropped cycle.
            log.warning("geocode unreachable: %s", exc)
            return None


def _area_of(result: dict[str, Any]) -> tuple[str | None, str | None]:
    """The district and the city one Geocoding result's `address_components`
    name, each the first component of its most specific type
    (`_DISTRICT_TYPES`, `_CITY_TYPES`). A district that is the city's own name
    is not a district."""
    components = result.get("address_components")
    if not isinstance(components, list):
        return None, None

    def first(types: tuple[str, ...]) -> str | None:
        for wanted in types:
            for c in components:
                if isinstance(c, dict) and wanted in (c.get("types") or ()):
                    name = str(c.get("long_name") or "").strip()
                    if name:
                        return name
        return None

    district, city = first(_DISTRICT_TYPES), first(_CITY_TYPES)
    return (None if district == city else district), city


def _first_address(payload: Any) -> str | None:
    """The first PLUS-CODE-FREE result's `formatted_address`, only when Google
    reported OK.

    A non-OK `status` (ZERO_RESULTS, OVER_QUERY_LIMIT, REQUEST_DENIED) arrives as
    a 200 with that status in the body — treat every non-OK as "no address",
    never as a partial result.

    Results are not ordered by usefulness (Google's own client docs): for a
    coordinate with no street address Google puts a PLUS CODE in
    `results[0]` — sometimes typed `premise`/`street_address`, not only
    `plus_code` — and the real `route` answer sits behind it (e.g.
    「香港新界元朗FQQQ+QQ」 ahead of 香港新界元朗南生圍路). A Plus
    Code is grid machinery nobody reads aloud, so any result whose
    formatted_address embeds one is skipped; the request-side
    `result_type` filter alone cannot catch the embedded premise form. ALL
    results plus-coded (open sea) is an honest None — `not_found`, never a
    grid reference dressed as an address.
    """
    result = _first_result(payload)
    return None if result is None else str(result["formatted_address"]).strip()


def _first_result(payload: Any) -> dict[str, Any] | None:
    """The result `_first_address` picks (the first plus-code-free one, only
    when Google reported OK), whole: its components name the areas."""
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    for result in results:
        if not isinstance(result, dict):
            continue
        address = str(result.get("formatted_address") or "").strip()
        if address and not _PLUS_CODE_IN_ADDRESS.search(address):
            return result
    return None
