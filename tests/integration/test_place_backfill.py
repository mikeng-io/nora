"""The retained geocode backfill on PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nora.app.place_backfill import backfill_places, render
from nora.domain.gatherings import Gathering, GatheringStatus
from nora.domain.ports import PlaceArea
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.migrations_runner import run_migrations
from tests.fixtures.gatherings import SeededGatheringStore

_AREA = PlaceArea(district="旺角 / Mong Kok", city="九龍 / Kowloon")


class _Geocoder:
    def __init__(self, area: PlaceArea = _AREA) -> None:
        self.asked: list[tuple[float, float]] = []
        self._area = area

    async def area_for(self, *, lat: float, lng: float) -> PlaceArea | None:
        self.asked.append((lat, lng))
        return self._area


def _event(place_id: str) -> Gathering:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    return Gathering(
        id=uuid4(), conversation_id="g1", title="Dinner", created_by="member-a",
        when=now + timedelta(days=3), venue_text="the place", venue_place_id=place_id,
        venue_name="Noodle House", venue_address=None, venue_rating=None,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=now,
        status=GatheringStatus.PROPOSED, reminder_id=None, created_at=now, unit_id="unit-a",
    )


async def test_backfill_updates_only_the_geocode_cache(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        events, cache = SeededGatheringStore(engine), SqlGeocodeCacheStore(engine)
        event = await events.create(_event("place-1"))
        await cache.put(lat_rounded=1.0, lng_rounded=2.0, address="cached")
        geocoder = _Geocoder()

        dry = await backfill_places(cache=cache, geocoder=geocoder)
        assert dry.geocodes.found == 1
        assert geocoder.asked == []
        assert "dry run" in render(dry)[0]

        done = await backfill_places(cache=cache, geocoder=geocoder, apply=True)
        assert done.geocodes.placed == 1
        assert geocoder.asked == [(1.0, 2.0)]
        assert await cache.area_within(lat=1.0, lng=2.0, radius_m=10.0) == _AREA
        retained = await events.get(event.id)
        assert retained is not None and retained.venue_district is None
        again = await backfill_places(cache=cache, geocoder=geocoder, apply=True)
        assert again.geocodes.found == 0
    finally:
        await engine.dispose()


async def test_a_cached_geocodes_area_is_screened_before_it_is_written(clean_pg_url):
    """The R-28 screen a venue's areas pass also stands before the geocode
    cache: an area name that reads as a coordinate is dropped, and the rest
    is written."""
    run_migrations(clean_pg_url)
    engine = create_sql_engine(clean_pg_url)
    cache = SqlGeocodeCacheStore(engine)
    await cache.put(lat_rounded=1.0, lng_rounded=2.0, address="cached")
    done = await backfill_places(
        cache=cache,
        geocoder=_Geocoder(PlaceArea(district=_AREA.district, city="1.5, 2.5")),
        apply=True, per_second=1000.0,
    )
    assert done.geocodes.placed == 1
    assert await cache.area_within(lat=1.0, lng=2.0, radius_m=10.0) == PlaceArea(
        district=_AREA.district,
    )
    await engine.dispose()
