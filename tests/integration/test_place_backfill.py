"""`nora memory backfill-places` (`nora.app.place_backfill`) on Postgres: the
venues and cached geocodes resolved before 0118 are given their areas through
the stores' own write paths, the event's venue is then placed in the memory
index, a dry run asks and writes nothing, and a second run finds nothing left
to ask. The geocoders are fakes that count what they are asked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa

from nora.app.place_backfill import backfill_places, render
from nora.domain.gatherings import Gathering, GatheringStatus
from nora.domain.models import NormalizedMessage
from nora.domain.ports import PlaceArea
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEMORY_EDGE

_AREA = PlaceArea(district="旺角 / Mong Kok", city="九龍 / Kowloon")


class _Places:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def area_of_place(self, place_id: str) -> PlaceArea | None:
        self.asked.append(place_id)
        return _AREA if place_id == "place-1" else None


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


async def test_a_dry_run_asks_nothing_and_apply_places_what_is_left_once(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    await SqlRecordStore(engine).append(
        message=NormalizedMessage("signal", "g1", "group", "+10000000001", "Member A", 1000,
                                  "dinner?", {}),
        person_id="member-a",
    )
    events, cache = SqlGatheringStore(engine), SqlGeocodeCacheStore(engine)
    two = [await events.create(_event("place-1")), await events.create(_event("place-1"))]
    await events.create(_event("place-2"))  # the geocoder names no area for it
    await cache.put(lat_rounded=1.0, lng_rounded=2.0, address="cached")
    places, geocoder = _Places(), _Geocoder()

    async def run(apply: bool):
        return await backfill_places(
            events=events, cache=cache, places=places, geocoder=geocoder, apply=apply,
            per_second=1000.0,
        )

    dry = await run(False)
    assert (dry.venues.found, dry.geocodes.found) == (3, 1)
    assert places.asked == [] and geocoder.asked == []
    assert "dry run" in render(dry)[0]

    done = await run(True)
    assert sorted(places.asked) == ["place-1", "place-2"]  # once per place, not per event
    assert (done.venues.placed, done.venues.unnamed, done.venues.failed) == (2, 1, 0)
    assert (done.geocodes.placed, done.geocodes.failed) == (1, 0)
    for e in two:
        got = await events.get(e.id)
        assert got is not None and (got.venue_district, got.venue_city) == (
            _AREA.district, _AREA.city,
        )
    assert await cache.area_within(lat=1.0, lng=2.0, radius_m=10.0) == _AREA
    async with engine.connect() as conn:
        chain = (await conn.execute(sa.select(MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.dst_id).where(
            MEMORY_EDGE.c.rel == "located_in",
        ))).all()
    assert {tuple(r) for r in chain} == {
        ("place-1", "district:旺角 / Mong Kok"),
        ("district:旺角 / Mong Kok", "city:九龍 / Kowloon"),
    }

    places.asked.clear()
    geocoder.asked.clear()
    again = await run(True)
    assert (again.venues.found, again.geocodes.found) == (1, 0)  # only the unnamed one
    assert places.asked == ["place-2"] and geocoder.asked == []
    await engine.dispose()


async def test_a_cached_geocodes_area_is_screened_before_it_is_written(pg_url):
    """The R-28 screen a venue's areas pass also stands before the geocode
    cache: an area name that reads as a coordinate is dropped, and the rest
    is written."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    events, cache = SqlGatheringStore(engine), SqlGeocodeCacheStore(engine)
    await cache.put(lat_rounded=1.0, lng_rounded=2.0, address="cached")
    done = await backfill_places(
        events=events, cache=cache, places=_Places(),
        geocoder=_Geocoder(PlaceArea(district=_AREA.district, city="1.5, 2.5")),
        apply=True, per_second=1000.0,
    )
    assert done.geocodes.placed == 1
    assert await cache.area_within(lat=1.0, lng=2.0, radius_m=10.0) == PlaceArea(
        district=_AREA.district,
    )
    await engine.dispose()
