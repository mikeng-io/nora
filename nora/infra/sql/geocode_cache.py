"""`GeocodeCacheStore` over SQLAlchemy Core — the durable half of
`CachingBilingualGeocoder`'s cache (`nora.domain.ports.GeocodeCacheStore`'s
own docstring has the ownership/boundary rationale).

**Never raises.** A cache read/write failure costs a re-geocode next time,
never the caller's answer — same discipline `SqlMediaDescriptionStore` follows
for the same reason.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.ports import PlaceArea
from nora.domain.presence import haversine_distance_m
from nora.infra.sql.schema import GEOCODE_CACHE

log = logging.getLogger("nora.infra.sql.geocode_cache")

# Degrees-per-metre at HK's latitude (~22.3°N), for the bounding-box prefilter
# `nearest_within` uses before the precise haversine pass — cheap because a
# derived place's `radius_m` (cluster_eps_m, default 100 m) makes the box
# tiny, and this table is expected to stay small (lazy, on-demand fills only).
_M_PER_DEG_LAT = 111_320.0


class SqlGeocodeCacheStore:
    """Satisfies `nora.domain.ports.GeocodeCacheStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, *, lat_rounded: float, lng_rounded: float) -> str | None:
        stmt = sa.select(GEOCODE_CACHE.c.address).where(
            GEOCODE_CACHE.c.lat_rounded == lat_rounded,
            GEOCODE_CACHE.c.lng_rounded == lng_rounded,
        )
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).first()
        except Exception:
            log.exception("geocode_cache read failed; treating as a miss")
            return None
        return None if row is None else str(row.address)

    async def put(
        self, *, lat_rounded: float, lng_rounded: float, address: str,
        area: PlaceArea | None = None,
    ) -> None:
        area = area or PlaceArea()
        stmt = pg_insert(GEOCODE_CACHE).values(
            lat_rounded=lat_rounded, lng_rounded=lng_rounded, address=address,
            district=area.district, city=area.city,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[GEOCODE_CACHE.c.lat_rounded, GEOCODE_CACHE.c.lng_rounded],
            set_={
                "address": stmt.excluded.address, "district": stmt.excluded.district,
                "city": stmt.excluded.city, "resolved_at": sa.text("now()"),
            },
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except Exception:
            # The caller already has the address and nothing to do differently.
            # Losing the write costs one re-geocode next time this coordinate
            # comes up, never the current answer.
            log.exception("geocode_cache write failed; the address is not cached")

    async def nearest_within(
        self, *, lat: float, lng: float, radius_m: float
    ) -> str | None:
        """The nearest cached address within `radius_m` of `(lat, lng)`, or
        None — `nora.analytics`' READ-ONLY label lookup (a derived place's
        centroid rarely lands on the exact rounded key a `where_is` question
        resolved, so this is a radius search, not `get`'s exact-key lookup)."""
        rows = await self._near(lat=lat, lng=lng, radius_m=radius_m)
        return None if not rows else str(rows[0].address)

    async def area_within(
        self, *, lat: float, lng: float, radius_m: float
    ) -> PlaceArea | None:
        """The areas of the nearest cached row within `radius_m` that has
        any, or None: `nora.analytics.units` places an outing by them."""
        for row in await self._near(lat=lat, lng=lng, radius_m=radius_m):
            area = PlaceArea(district=row.district, city=row.city)
            if area:
                return area
        return None

    async def _near(self, *, lat: float, lng: float, radius_m: float) -> list[Any]:
        """Rows within `radius_m`, nearest first; empty on a read failure."""
        dlat = radius_m / _M_PER_DEG_LAT
        cos_lat = math.cos(math.radians(lat)) or 1e-9
        dlng = radius_m / (_M_PER_DEG_LAT * cos_lat)
        stmt = sa.select(
            GEOCODE_CACHE.c.lat_rounded, GEOCODE_CACHE.c.lng_rounded, GEOCODE_CACHE.c.address,
            GEOCODE_CACHE.c.district, GEOCODE_CACHE.c.city,
        ).where(
            GEOCODE_CACHE.c.lat_rounded.between(lat - dlat, lat + dlat),
            GEOCODE_CACHE.c.lng_rounded.between(lng - dlng, lng + dlng),
        )
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        except Exception:
            log.exception("geocode_cache radius read failed; treating as a miss")
            return []
        near = [
            (haversine_distance_m(lat, lng, r.lat_rounded, r.lng_rounded), i, r)
            for i, r in enumerate(rows)
        ]
        return [r for d, _, r in sorted(near, key=lambda t: (t[0], t[1])) if d <= radius_m]
