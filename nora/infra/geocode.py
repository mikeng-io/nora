"""Reverse geocode one coordinate to a place name — or None. NEVER raises.

Life360 attaches a coarse `address1` to most fixes, so this is the FALLBACK for
the ones it does not (design/19 §5): a fix Nora would otherwise have to report as
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
(The earlier «design/14 §2.2 on-host geocoding» citation was wrong — the call is
off-host to Google; a proper decision record for the collector's coordinate
egress is a governance follow-up flagged by the 2026-08-08 audit.)

Uses the classic Geocoding API, which is a GET with the key in the query string
(NOT the `X-Goog-Api-Key` header the Routes/Places v1 endpoints use — a different
Google product, a different auth shape). Like every outbound helper in this
codebase it degrades to None on any failure: an address is a nice-to-have, never
load-bearing, so a geocode outage must cost a coordinate-only fix, never a raised
exception in the collector loop or a stalled cycle.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger("nora.geocode")

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


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

    def __init__(self) -> None:
        # (rounded lat, rounded lng) -> address. Only successful lookups are
        # cached; a miss (None) is NOT stored, so a transient geocode outage does
        # not pin a coordinate to "no address" for the life of the process.
        self._cache: dict[tuple[float, float], str] = {}

    async def address_for(self, *, lat: float, lng: float) -> str | None:
        """The best formatted address for `lat,lng` in BOTH languages, or None.
        NEVER raises.

        The group reads both, and Life360's own label was bilingual («雲杉徑 Cloudfir Path»), so store both: a backend returns ONE language per call, so
        this asks for `zh` and `en` and joins them «中文 / English» (Chinese first —
        a Cantonese group). One language missing still yields the other; both
        missing is None (the caller falls back to Life360's label, or the raw
        coordinate). Two calls per NEW coordinate, but the ~11 m cache means a
        stationary member pays it once, not every cycle.
        """
        key = (round(lat, self._CACHE_PRECISION), round(lng, self._CACHE_PRECISION))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        zh = await self._fetch(lat, lng, "zh")
        en = await self._fetch(lat, lng, "en")
        if zh and en and zh != en:
            address: str | None = f"{zh} / {en}"
        else:
            address = zh or en
        if address is not None:
            if len(self._cache) >= self._CACHE_MAX:
                # Drop the oldest entry (dicts preserve insertion order) so a
                # long-running collector's cache stays bounded.
                del self._cache[next(iter(self._cache))]
            self._cache[key] = address
        return address

    async def _fetch(self, lat: float, lng: float, language: str) -> str | None:
        raise NotImplementedError


class GoogleGeocoder(CachingBilingualGeocoder):
    """`lat, lng -> formatted address` against the Google Geocoding API.

    Constructed with the resolved key (DEC-8: from the environment at the
    composition root, never read here). Built only when a key is present; a
    collector with no key simply has no geocoder and stores the coordinate as-is.
    """

    def __init__(self, *, api_key: str, timeout_seconds: float = 5.0) -> None:
        super().__init__()
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def _fetch(self, lat: float, lng: float, language: str) -> str | None:
        """One Google reverse-geocode in `language` (`"zh"` → Google's `zh-HK`).
        None on any failure — a non-200, a non-OK envelope status, non-JSON, or a
        transport error — so a missing address is never a dropped cycle."""
        lang = "zh-HK" if language == "zh" else "en"
        params = {"latlng": f"{lat},{lng}", "key": self._api_key, "language": lang}
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
                    payload = await response.json(content_type=None)
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
        return _first_address(payload)


def _first_address(payload: Any) -> str | None:
    """The first result's `formatted_address`, only when Google reported OK.

    A non-OK `status` (ZERO_RESULTS, OVER_QUERY_LIMIT, REQUEST_DENIED) arrives as
    a 200 with that status in the body — treat every non-OK as "no address",
    never as a partial result.
    """
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    address = str(first.get("formatted_address") or "").strip()
    return address or None
