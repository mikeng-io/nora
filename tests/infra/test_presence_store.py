"""`PresenceStore` against a REAL Postgres (DEC-55).

The load-bearing test is the change-recorder dedup: two reads of an UNMOVED
member produce ONE row. Revert `record_fix`'s `>` guard and
`test_an_unmoved_member_is_recorded_once` goes red — the whole point of the
collector being a change-recorder rather than a sampler.
"""

from __future__ import annotations

import pytest

from nora.domain.presence import PresenceFix, WireState
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.presence import PresenceStore

_DAY_MS = 86_400_000


def _fix(*, ts: int, person="p1", circle="c1", lat=22.44, lng=114.02,
         state=WireState.LIVE, recorded_at=None, **kw) -> PresenceFix:
    return PresenceFix(
        circle_id=circle, person_id=person, ts=ts, lat=lat, lng=lng,
        wire_state=state, recorded_at=ts if recorded_at is None else recorded_at, **kw,
    )


@pytest.fixture
async def make_store(pg_url):
    run_migrations(pg_url)
    created = []

    def _make(*, retention_days=180):
        s = PresenceStore(
            create_sql_engine(pg_url), retention_days=retention_days
        )
        created.append(s)
        return s

    yield _make
    for s in created:
        await s.aclose()


@pytest.fixture
async def store(make_store):
    return make_store()


async def test_record_fix_writes_a_row_when_ts_is_new(store):
    assert await store.record_fix(fix=_fix(ts=1000)) is True
    latest = await store.latest_fix(circle_id="c1", person_id="p1")
    assert latest is not None
    assert latest.ts == 1000
    assert latest.wire_state is WireState.LIVE


async def test_an_unmoved_member_is_recorded_once(store):
    """R-32: the change-recorder dedup. The daemon polls the
    same fix repeatedly (a stationary phone holds one fix for minutes); only the
    FIRST poll writes a row. Two reads of an unmoved member → one row. Revert the
    `>` guard in `record_fix` and this goes red."""
    same = _fix(ts=5000)
    assert await store.record_fix(fix=same) is True
    assert await store.record_fix(fix=same) is False    # identical ts — skipped
    assert await store.record_fix(fix=_fix(ts=5000, lat=1.0, lng=2.0)) is False  # older-or-equal
    window = await store.window(
        circle_id="c1", person_id="p1", since_ts=0, until_ts=10_000
    )
    assert len(window) == 1


async def test_a_moved_member_advances_the_series(store):
    for ts in (1000, 2000, 3000):
        assert await store.record_fix(fix=_fix(ts=ts)) is True
    assert (await store.latest_fix(circle_id="c1", person_id="p1")).ts == 3000
    window = await store.window(
        circle_id="c1", person_id="p1", since_ts=0, until_ts=10_000
    )
    assert [f.ts for f in window] == [1000, 2000, 3000]     # oldest first


async def test_an_out_of_order_fix_is_not_recorded(store):
    """A late-arriving older fix must never displace or duplicate the record —
    the guard is `> last_ts`, so a ts below the stored max is skipped."""
    assert await store.record_fix(fix=_fix(ts=3000)) is True
    assert await store.record_fix(fix=_fix(ts=2000)) is False
    assert (await store.latest_fix(circle_id="c1", person_id="p1")).ts == 3000


async def test_dedup_is_per_member(store):
    assert await store.record_fix(fix=_fix(ts=1000, person="p1")) is True
    # A different member with the SAME ts is a distinct series — recorded.
    assert await store.record_fix(fix=_fix(ts=1000, person="p2")) is True
    assert (await store.latest_fix(circle_id="c1", person_id="p2")).person_id == "p2"


async def test_latest_fix_is_none_when_nothing_recorded(store):
    assert await store.latest_fix(circle_id="c1", person_id="nobody") is None


async def test_optional_fields_round_trip(store):
    await store.record_fix(fix=_fix(
        ts=1000, accuracy_m=12.5, is_driving=True, battery=88.0, address="Yoho Mall",
        speed_kmh=42.3,
    ))
    f = await store.latest_fix(circle_id="c1", person_id="p1")
    assert f.accuracy_m == 12.5
    assert f.is_driving is True
    assert f.battery == 88.0
    assert f.address == "Yoho Mall"
    assert f.speed_kmh == 42.3


async def test_window_is_bounded_and_scoped(store):
    for ts in (1000, 2000, 3000, 4000):
        await store.record_fix(fix=_fix(ts=ts))
    await store.record_fix(fix=_fix(ts=2500, person="other"))
    win = await store.window(circle_id="c1", person_id="p1", since_ts=2000, until_ts=3000)
    assert [f.ts for f in win] == [2000, 3000]


async def test_prune_deletes_only_fixes_older_than_retention(make_store):
    store = make_store(retention_days=180)
    now = 1_000 * _DAY_MS
    old = now - 200 * _DAY_MS       # past retention
    fresh = now - 10 * _DAY_MS      # inside retention
    assert await store.record_fix(fix=_fix(ts=old)) is True
    assert await store.record_fix(fix=_fix(ts=fresh)) is True
    deleted = await store.prune(now_ms=now)
    assert deleted == 1
    remaining = await store.window(circle_id="c1", person_id="p1", since_ts=0, until_ts=now)
    assert [f.ts for f in remaining] == [fresh]


async def test_prune_is_a_no_op_when_all_fixes_are_fresh(store):
    now = 1_000 * _DAY_MS
    await store.record_fix(fix=_fix(ts=now - _DAY_MS))
    assert await store.prune(now_ms=now) == 0


# ── saved places ─────────────────────────────────────────────────────────────

async def test_replace_places_round_trips_and_removes_what_the_wire_dropped(store):
    """Wholesale replacement: a place gone from the wire is gone from the table
    — the delete-then-insert transaction, not an upsert that leaves ghosts."""
    from nora.domain.presence import SavedPlace

    def place(pid, name):
        return SavedPlace(circle_id="c1", place_id=pid, name=name,
                          lat=22.44, lng=114.02, radius_m=80.0, updated_ms=1000)

    await store.replace_places(circle_id="c1", places=[place("p1", "Home"),
                                                       place("p2", "School")])
    assert [p.name for p in await store.places_for(circle_id="c1")] == [
        "Home", "School",
    ]
    await store.replace_places(circle_id="c1", places=[place("p1", "Home")])
    assert [p.name for p in await store.places_for(circle_id="c1")] == ["Home"]
    # Another circle's places are untouched by c1's replacement.
    await store.replace_places(
        circle_id="c2",
        places=[SavedPlace(circle_id="c2", place_id="px", name="Office",
                           lat=22.28, lng=114.17, radius_m=None, updated_ms=1000)],
    )
    await store.replace_places(circle_id="c1", places=[])
    assert await store.places_for(circle_id="c1") == []
    assert [p.name for p in await store.places_for(circle_id="c2")] == ["Office"]
