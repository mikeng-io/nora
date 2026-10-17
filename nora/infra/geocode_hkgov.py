"""HK-government reverse-geocode — a SWITCHABLE `ReverseGeocoder` option, not a
replacement for Google.

Backend: the LandsD / hkmapservice ArcGIS `GeocodeServer/reverseGeocode`
operation. Its service URL and access token come from the operator's LANDSD Map
API portal account — a key issued to GOVERNMENT bureaux/departments
(mapapi@landsd.gov.hk), so the token rides in from the environment at the
composition root (DEC-8), exactly like Google's key, and the service URL is
deployment config because the portal issues it per account.

Standard Esri `reverseGeocode` contract (what this parses):

    GET {service_url}?location={lng},{lat}&f=json&token={token}&langCode={lang}&outSR=4326
    -> {"address": {"LongLabel": "...", "Match_addr": "...", ...}, "location": …}
    (a failure arrives as a 200 with an {"error": …} object, never a 4xx)

The exact `langCode` strings the HK locator honours are configurable (`lang_zh`/
`lang_en`) because the portal's locators vary; the request/parse shape follows
the documented ArcGIS standard. The caching, bilingual join, and fail-soft
discipline are inherited from `CachingBilingualGeocoder` and covered by the
contract suite; whether a deployment's locator matches the standard shape is a
config question, not a code one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from nora.infra.geocode import CachingBilingualGeocoder, Geocoded

if TYPE_CHECKING:
    from nora.domain.ports import GeocodeCacheStore
    from nora.infra.geocode import AreaScreen

log = logging.getLogger("nora.geocode")


class HKGovGeocoder(CachingBilingualGeocoder):
    """`lat, lng -> formatted address` against the HK-gov ArcGIS reverse-geocode.

    `service_url` is the full `.../reverseGeocode` operation URL from the portal;
    `token` is the resolved access token (from the environment, never read here).
    """

    def __init__(
        self,
        *,
        service_url: str,
        token: str,
        timeout_seconds: float = 5.0,
        lang_zh: str = "zh",
        lang_en: str = "en",
        cache_store: GeocodeCacheStore | None = None,
        area_screen: AreaScreen | None = None,
    ) -> None:
        super().__init__(cache_store=cache_store, area_screen=area_screen)
        self._service_url = service_url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._lang = {"zh": lang_zh, "en": lang_en}

    async def _fetch(self, lat: float, lng: float, language: str) -> Geocoded | None:
        """One HK-gov reverse-geocode in `language`. None on any failure — a
        non-200, an ArcGIS `error` envelope, non-JSON, or a transport error — so a
        missing address is never a dropped cycle. `location` is `lng,lat` (Esri
        orders it x,y)."""
        params = {
            "location": f"{lng},{lat}",
            "f": "json",
            "token": self._token,
            "langCode": self._lang.get(language, language),
            "outSR": "4326",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(self._service_url, params=params) as response,
            ):
                if response.status != 200:
                    # The token rides in the query; never log the URL or params.
                    log.warning("hkgov geocode answered %s", response.status)
                    return None
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    log.warning("hkgov geocode did not return JSON")
                    return None
        except Exception as exc:  # noqa: BLE001
            # Broad and never re-raised: this runs inside the collector loop, so
            # any failure is a missing address, not a dropped cycle.
            log.warning("hkgov geocode unreachable: %s", exc)
            return None
        address = _hkgov_address(payload)
        if address is None:
            return None
        district, city = _hkgov_area(payload)
        return Geocoded(address, district=district, city=city)


def _hkgov_address(payload: Any) -> str | None:
    """The formatted address from an Esri `reverseGeocode` envelope.

    ArcGIS reports failure as a 200 body carrying an `error` object (never a
    partial result), so treat any `error` — or a missing `address` — as "no
    address". Prefer the fullest label the locator returns.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    address = payload.get("address")
    if not isinstance(address, dict):
        return None
    for field in ("LongLabel", "Match_addr", "Address"):
        value = str(address.get(field) or "").strip()
        if value:
            return value
    return None


def _hkgov_area(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """The district and the city an Esri `reverseGeocode` address names:
    `Neighborhood` (else `District`) and `City` (else `Region`), the
    standard address fields. A district that is the city's own name is not
    a district; a locator that fills none answers no area."""
    address = payload.get("address") or {}

    def first(*fields: str) -> str | None:
        for f in fields:
            value = str(address.get(f) or "").strip()
            if value:
                return value
        return None

    district, city = first("Neighborhood", "District"), first("City", "Region")
    return (None if district == city else district), city
