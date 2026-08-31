"""`ParkingClient` — real-time HK car-park vacancy behind the `parking_status`
tool, from the Transport Department's open-data feed (data.gov.hk, hk-td-tis_5).

The feed is a small index plus one tiny JSON per car park:
- `car_park_list.csv` (~105KB, hundreds of carparks): Parking ID, name EN/TC/SC,
  basic-info URL, vacancy URL.
- Per-parking vacancy JSON (~179 bytes): `{"car_park":[{"park_id":"...",
  "vehicle_type":[{"type":"P","service_category":[{"category":"HOURLY",
  "vacancy_type":"A","vacancy":30,"lastupdate":"..."}]}]}]}`.
- Per-parking basic-info JSON (~500 bytes): displayAddress, latitude/longitude,
  district, opening_status, height, remark (rate table).

The MODEL never sees the 105KB index: the client caches it in-process and
matches a venue name to ONE parking id, then fetches that one small JSON.
Same posture as `TransitClient` — nothing here raises; a failure is
`None` (the tool reports `unavailable`), never a fabricated number.

URLs and timeout are constants here (published government endpoints) but
constructor parameters so tests can point at stand-ins.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import aiohttp

log = logging.getLogger("nora.parking")

INDEX_URL = "https://static.data.gov.hk/td/parking-vacancy/dataspec/car_park_list.csv"
RESOURCE_URL = "https://resource.data.one.gov.hk/td/carpark/"

TIMEOUT_SECONDS = 5.0


class ParkingClient:
    """Live HK car-park vacancy, one named venue at a time.

    Constructed once per process; the index CSV cache lives on it so a
    repeated question does not re-pull 105KB every time.
    """

    def __init__(
        self,
        *,
        index_url: str = INDEX_URL,
        resource_url: str = RESOURCE_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._index_url = index_url
        self._resource_url = resource_url.rstrip("/") + "/"
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        # name-normalised -> (parking_id, name_en, name_tc, vacancy_url)
        self._index: dict[str, tuple[str, str, str, str]] | None = None

    async def vacancy(self, *, name: str) -> dict[str, Any] | None:
        """Vacancy for one named venue, or None (unavailable / not found).

        The caller (`parking_status`) distinguishes `nothing_found` from
        `unavailable`; this returns `None` for both, matching the
        `ParkingProvider` port's "failure is a missing reading" shape.
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return None
        try:
            index = await self._load_index()
        except Exception:
            log.exception("parking index fetch failed")
            return None
        if index is None:
            return None
        hit = self._match(index, cleaned)
        if hit is None:
            return None
        parking_id, name_en, name_tc, vacancy_url = hit
        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, raise_for_status=True,
            ) as session:
                async with session.get(vacancy_url) as resp:
                    # utf-8-sig: the vacancy bodies carry a BOM too (same feed
                    # convention as the index CSV) — measured 2026-08-31.
                    body = await resp.json(encoding="utf-8-sig")
        except Exception:
            log.exception("parking vacancy fetch failed")
            return None
        vacancy = _vacancy_of(body)
        if vacancy is None:
            return None
        return {
            "parking_id": parking_id,
            "name": name_tc or name_en,
            "vacancy": vacancy,
            "lastupdate": _lastupdate_of(body),
            "status": "OPEN",
            "address": None,
        }

    async def _load_index(self) -> dict[str, tuple[str, str, str, str]] | None:
        if self._index is not None:
            return self._index
        async with aiohttp.ClientSession(
            timeout=self._timeout, raise_for_status=True,
        ) as session:
            async with session.get(self._index_url) as resp:
                # utf-8-sig strips the CSV's BOM — otherwise the first header
                # is "\ufeffParking ID" and `row.get("Parking ID")` misses
                # every row, leaving the index empty (measured 2026-08-31).
                text = await resp.text(encoding="utf-8-sig")
        index: dict[str, tuple[str, str, str, str]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            pid = (row.get("Parking ID") or "").strip()
            name_en = (row.get("Car Park Name (English)") or "").strip()
            name_tc = (row.get("Car Park Name (Traditional Chinese)") or "").strip()
            vacancy_url = (row.get("Parking Vacancy Data") or "").strip()
            if not pid or not name_en or not vacancy_url:
                continue
            # Register all three name columns (normalised) -> same record.
            for label in (name_en, name_tc, name_en.lower()):
                key = _normalise(label)
                if key:
                    index[key] = (pid, name_en, name_tc, vacancy_url)
        if not index:
            return None
        self._index = index
        return index

    def _match(
        self, index: dict[str, tuple[str, str, str, str]], name: str,
    ) -> tuple[str, str, str, str] | None:
        key = _normalise(name)
        if key in index:
            return index[key]
        # Distinctive-substring fallback: a venue name usually CONTAINS the
        # car park's name («太古廣場」→ «Pacific Place»/「太古廣場」) or vice
        # versa. Longest match wins to avoid picking a prefix like "Park".
        best: tuple[int, tuple[str, str, str, str]] | None = None
        for k, rec in index.items():
            if not k:
                continue
            if key and (key in k or k in key):
                score = min(len(key), len(k))
                if best is None or score > best[0]:
                    best = (score, rec)
        return best[1] if best else None


def _normalise(text: str) -> str:
    """Lowercase, strip spaces/CJK gaps — «Pacific Place» == «pacificplace»."""
    return "".join(ch for ch in text.lower() if not ch.isspace())


def _vacancy_of(body: Any) -> int | None:
    """First HOURLY vacancy count from the feed body, or None."""
    try:
        for cp in body.get("car_park", []):
            for vt in cp.get("vehicle_type", []):
                for sc in vt.get("service_category", []):
                    if sc.get("category") == "HOURLY":
                        v = sc.get("vacancy")
                        if isinstance(v, int):
                            return v
    except Exception:
        return None
    return None


def _lastupdate_of(body: Any) -> str | None:
    try:
        for cp in body.get("car_park", []):
            for vt in cp.get("vehicle_type", []):
                for sc in vt.get("service_category", []):
                    if sc.get("category") == "HOURLY" and sc.get("lastupdate"):
                        return str(sc["lastupdate"])
    except Exception:
        return None
    return None