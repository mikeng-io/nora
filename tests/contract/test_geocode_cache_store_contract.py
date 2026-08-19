"""`GeocodeCacheStore` has two implementations, so it gets one suite both
pass — CLAUDE.md's rule. The property that matters: a coordinate nobody has
ever resolved must come back as a miss (None), never a fabricated address,
and a resolved coordinate must round-trip byte-identical."""

from __future__ import annotations

import pytest

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeGeocodeCacheStore


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeGeocodeCacheStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield SqlGeocodeCacheStore(engine)
    await engine.dispose()


async def test_an_unresolved_coordinate_is_a_miss(store):
    assert await store.get(lat_rounded=22.4106, lng_rounded=114.1245) is None


async def test_put_then_get_round_trips(store):
    await store.put(
        lat_rounded=22.4106, lng_rounded=114.1245, address="Yoho Mall, Yuen Long",
    )
    got = await store.get(lat_rounded=22.4106, lng_rounded=114.1245)
    assert got == "Yoho Mall, Yuen Long"


async def test_a_different_rounded_coordinate_still_misses(store):
    await store.put(
        lat_rounded=22.4106, lng_rounded=114.1245, address="Yoho Mall, Yuen Long",
    )
    assert await store.get(lat_rounded=22.4000, lng_rounded=114.2000) is None


async def test_putting_the_same_coordinate_twice_is_not_an_error(store):
    """A restart-recovered process may re-resolve a coordinate already cached
    (e.g. an in-flight geocode that raced a previous write) — this must
    overwrite, not raise a constraint violation that costs the answer."""
    await store.put(lat_rounded=22.4106, lng_rounded=114.1245, address="first")
    await store.put(lat_rounded=22.4106, lng_rounded=114.1245, address="second")
    assert await store.get(lat_rounded=22.4106, lng_rounded=114.1245) == "second"


async def test_nearest_within_finds_a_cached_point_close_by(store):
    """`nora.analytics` queries by a derived place's CENTROID, which rarely
    matches the exact rounded key someone's `where_is` question resolved —
    this is a radius search, not an exact-key lookup."""
    await store.put(lat_rounded=22.4106, lng_rounded=114.1245, address="Yoho Mall")
    got = await store.nearest_within(lat=22.4106, lng=114.1245, radius_m=100.0)
    assert got == "Yoho Mall"


async def test_nearest_within_returns_none_when_nothing_is_close(store):
    await store.put(lat_rounded=22.4106, lng_rounded=114.1245, address="Yoho Mall")
    got = await store.nearest_within(lat=22.5000, lng=114.5000, radius_m=100.0)
    assert got is None


async def test_nearest_within_picks_the_closer_of_two_cached_points(store):
    await store.put(lat_rounded=22.4106, lng_rounded=114.1245, address="near")
    await store.put(lat_rounded=22.3250, lng_rounded=114.1750, address="far")
    got = await store.nearest_within(lat=22.4106, lng=114.1245, radius_m=2000.0)
    assert got == "near"
