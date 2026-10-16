"""Street (metered) parking — the TD on-street feed behind `find_parking`.

Two small-to-medium CSVs from the Transport Department (hk-td-msd_1):
- `parkingspaces.csv` (~4.8MB, ~20.7k rows): every metered space with its
  ParkingSpaceId, street, district/subdistrict and **Latitude/Longitude**.
- `occupancystatus.csv` (~716KB, ~20.7k rows): per-space
  ParkingMeterStatus (N = new meter) and OccupancyStatus (O/OCCUPIED or
  V/VACANT) — the live on-street occupancy.

The MODEL never sees these files: exactly like `ParkingClient`'s index, this
module caches the space table in-process and answers one question: «how many
metered spaces and how many VACANT are within R metres of this point, on
what streets». That is the street-parking row `find_parking` appends — on-
street availability is a FIND-layer answer, no extra tool.

Nothing here raises; a failure collapses to `None` (the parent tool reports
`unavailable`). The occupancy file is tiny and re-fetched per call; the
space table is ~5MB and cached for the process lifetime (it changes only
when TD installs/pulls meters).
"""

from __future__ import annotations

import csv
import io
import logging
import math

import aiohttp

from ...domain.parking import StreetParking as StreetParking

log = logging.getLogger("nora.parking")

SPACES_URL = "https://resource.data.one.gov.hk/td/psiparkingspaces/spaceinfo/parkingspaces.csv"
OCCUPANCY_URL = "https://resource.data.one.gov.hk/td/psiparkingspaces/occupancystatus/occupancystatus.csv"

TIMEOUT_SECONDS = 8.0

# A street-parking search radius: metered bays cluster along a block, so
# 300 m is «on this street» while 1500 m (the nearby-parking radius) would
# sweep several districts. Mirrors the walkable-lot argument in nearby.py.
STREET_RADIUS_M = 300.0


class StreetParkingClient:
    """Cached metered-space table + fresh occupancy, one question at a time.

    Constructed once per process (like `ParkingClient`); the 4.8MB space
    table is fetched on first use and cached, the small occupancy file is
    always fresh.
    """

    def __init__(
        self,
        *,
        spaces_url: str = SPACES_URL,
        occupancy_url: str = OCCUPANCY_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._spaces_url = spaces_url
        self._occupancy_url = occupancy_url
        self._timeout_seconds = timeout_seconds
        # (lat, lng, street_tc, parking_space_id)
        self._spaces: tuple[tuple[float, float, str, str], ...] | None = None

    async def near(
        self, *, lat: float, lng: float, radius_m: float = STREET_RADIUS_M,
    ) -> StreetParking | None:
        """Metered-space counts within `radius_m` of (lat, lng). None = failed."""
        try:
            spaces = await self._load_spaces()
            occupied = await self._occupancy()
        except Exception:
            log.exception("street parking fetch failed")
            return None
        if spaces is None or occupied is None:
            return None
        # Filter by distance; count total and vacant and collect street names.
        near = [
            (s, occupied.get(s[3], ""))
            for s in spaces if _metres(lat, lng, s[0], s[1]) <= radius_m
        ]
        if not near:
            return StreetParking(total=0, vacant=0, streets=())
        streets: list[str] = []
        seen: set[str] = set()
        vacant = sum(1 for _s, occ in near if occ == "V")
        for s, _occ in near:
            st = s[2].strip()
            if st and st not in seen:
                seen.add(st)
                streets.append(st)
        return StreetParking(
            total=len(near), vacant=vacant, streets=tuple(streets[:5]),
        )

    async def _load_spaces(self) -> tuple[tuple[float, float, str, str], ...] | None:
        if self._spaces is not None:
            return self._spaces
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
            raise_for_status=True,
        ) as session, session.get(self._spaces_url) as resp:
            text = await resp.text(encoding="utf-8-sig")
        out: list[tuple[float, float, str, str]] = []
        lines = text.splitlines()
        # The TD file opens with a date line, an empty line, THEN the real
        # header (row 0 = a date, row 1 = empty,
        # row 2 = ParkingSpaceId,...). DictReader would take the date line as
        # its fieldnames and every data row would read None.
        for _idx, line in enumerate(lines):
            if "ParkingSpaceId" in line and "Latitude" in line:
                break
        else:
            return None
        for row in csv.DictReader(lines[_idx:], fieldnames=None):
            try:
                lat = float(row.get("Latitude") or "")
                lng = float(row.get("Longitude") or "")
            except (TypeError, ValueError):
                continue
            out.append((
                lat, lng,
                (row.get("Street_tc") or row.get("Street") or "").strip(),
                (row.get("ParkingSpaceId") or "").strip(),
            ))
        # An empty parse after a real header is a FAILURE (renamed column,
        # coordinates moved), not «no metered bays anywhere» — leave the
        # cache unset so the next call retries and the tool reports
        # unavailable rather than a confident zero.
        if not out:
            log.warning("street spaces parse yielded zero rows from a real header")
            return None
        self._spaces = tuple(out)
        return self._spaces

    async def _occupancy(self) -> dict[str, str] | None:
        """ParkingSpaceId -> OccupancyStatus, always fresh."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
            raise_for_status=True,
        ) as session, session.get(self._occupancy_url) as resp:
            text = await resp.text(encoding="utf-8-sig")
        out: dict[str, str] = {}
        for row in csv.DictReader(io.StringIO(text)):
            pid = (row.get("ParkingSpaceId") or "").strip()
            status = (row.get("OccupancyStatus") or "").strip()
            if pid:
                out[pid] = status
        return out or None


def _metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dy = (lat2 - lat1) * 111_320.0
    dx = (lng2 - lng1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)
