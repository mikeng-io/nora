"""Reverse geocode one coordinate to a place name — or None. NEVER raises.

Life360 attaches a coarse `address1` to most fixes, so this is the FALLBACK for
the ones it does not (design/19 §5): a fix Nora would otherwise have to report as
bare «22.31, 114.16», turned into «Yoho Mall» so she can answer «where are they»
with a place.

It lives here, at the infra level and NOT under `nora.infra.routes`, on purpose:
the caller is the presence COLLECTOR, and coupling the collector to Nora's tool
infra would drag the whole routing package into a second process for one call.
The collector is also the RIGHT place for it (design/14 §2.2: a stored position
may seed an on-host geocoding call) — doing it here means whereis stays a pure
local-DB read that never itself sends a coordinate anywhere.

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


class ReverseGeocoder:
    """`lat, lng -> formatted address` against the Google Geocoding API.

    Constructed with the resolved key (DEC-8: from the environment at the
    composition root, never read here). Built only when a key is present; a
    collector with no key simply has no geocoder and stores the coordinate as-is.
    """

    # Round to ~11 m (4 dp) for the memo key: a stationary member reports a
    # jittering-but-nearly-identical coordinate every cycle, and re-billing a
    # geocode for the same doorstep every 90 s is the cost this cache exists to
    # cut. Bounded so a mover who visits many places cannot grow it without limit.
    _CACHE_PRECISION = 4
    _CACHE_MAX = 512

    def __init__(self, *, api_key: str, timeout_seconds: float = 5.0) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        # (rounded lat, rounded lng) -> address. Only successful lookups are
        # cached; a miss (None) is NOT stored, so a transient geocode outage does
        # not pin a coordinate to "no address" for the life of the process.
        self._cache: dict[tuple[float, float], str] = {}

    async def address_for(self, lat: float, lng: float) -> str | None:
        """The best formatted address for `lat,lng`, or None. NEVER raises.

        None means "no address to store" for every reason — a non-200, an
        `OVER_QUERY_LIMIT`/`ZERO_RESULTS` status in Google's envelope, a body
        that is not JSON, or any transport error. The caller falls back to the
        raw coordinate exactly as if this had not run.
        """
        key = (round(lat, self._CACHE_PRECISION), round(lng, self._CACHE_PRECISION))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        params = {"latlng": f"{lat},{lng}", "key": self._api_key}
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
        address = _first_address(payload)
        if address is not None:
            if len(self._cache) >= self._CACHE_MAX:
                # Drop the oldest entry (dicts preserve insertion order) so a
                # long-running collector's cache stays bounded.
                del self._cache[next(iter(self._cache))]
            self._cache[key] = address
        return address


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
