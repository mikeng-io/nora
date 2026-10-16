from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nora.domain.mobility import hour_of_week
from nora.infra.sql import mobility as mobility_sql
from nora.infra.sql.mobility import SqlMobilityStore


# The store is BOUND to a build here: these tests script the engine's answers
# statement by statement, and an unbound read first resolves the current build
# (`_read_build`), which the no-build tests in `tests/test_where_been.py` and
# `tests/web/test_presence_analytics.py` exercise against a real database.
class _Result:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = iter(results)
        self.statements = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return next(self.results)


class _Engine:
    def __init__(self, results: list[_Result]) -> None:
        self.connection = _Connection(results)

    def connect(self) -> _Connection:
        return self.connection


def _ts(year: int, month: int, day: int, hour: int) -> int:
    return int(
        datetime(year, month, day, hour, tzinfo=ZoneInfo("Asia/Hong_Kong")).timestamp()
        * 1000
    )


async def test_rhythm_bins_uses_the_substrate_local_hour_of_week(monkeypatch):
    start = _ts(2026, 8, 17, 9)  # Monday 09:00 HKT, bucket 9.
    engine = _Engine([
        _Result([SimpleNamespace(started_ts=start, ended_ts=start + 30 * 60_000)]),
    ])
    monkeypatch.setattr(mobility_sql.time, "time", lambda: (start + 86_400_000) / 1000)

    bins = await SqlMobilityStore(engine, build_id=1).rhythm_bins(
        subject_id="miles", window_ms=24 * 60 * 60 * 1000,
    )

    assert bins == [{"hour_of_week": hour_of_week(start, "Asia/Hong_Kong"),
                     "stay_count": 1, "total_dwell_ms": 30 * 60_000}]


async def test_rhythm_bins_splits_an_overnight_stay_across_the_hours_it_covers(monkeypatch):
    # The whole 9-hour stay spreads over the hours it spans, not onto its START
    # hour (Monday 22:00), which would leave the small hours looking empty.
    start = _ts(2026, 8, 17, 22)  # Monday 22:00 HKT
    end = start + 9 * 3_600_000   # Tuesday 07:00
    engine = _Engine([_Result([SimpleNamespace(started_ts=start, ended_ts=end)])])
    monkeypatch.setattr(mobility_sql.time, "time", lambda: (end + 3_600_000) / 1000)

    bins = await SqlMobilityStore(engine, build_id=1).rhythm_bins(
        subject_id="miles", window_ms=2 * 86_400_000,
    )

    assert [b["hour_of_week"] for b in bins] == [22, 23, *range(24, 31)]
    assert all(b["total_dwell_ms"] == 3_600_000 and b["stay_count"] == 1 for b in bins)


async def test_places_and_routes_reads_are_select_only_and_capped():
    engine = _Engine([
        _Result([
            SimpleNamespace(
                id=1, kind="home", label=None, lat=22.3, lng=114.2,
                radius_m=120.0, confidence=0.5, first_seen_ts=1, last_seen_ts=2,
                saved_place_id=None,
            ),
            SimpleNamespace(
                id=2, kind="unlabeled", label="Gym", lat=22.4, lng=114.2,
                radius_m=100.0, confidence=0.0, first_seen_ts=3, last_seen_ts=4,
                saved_place_id="l360-gym",
            ),
        ]),
        # Aggregates come from the rollup's LINKS (place_id), not a second
        # radius test: place 2 has no linked stay yet and reads as zero.
        _Result([SimpleNamespace(place_id=1, stay_count=1, total_dwell_ms=60_000)]),
        _Result([]),
        _Result([]),
    ])
    store = SqlMobilityStore(engine, build_id=1)

    assert await store.places_with_aggregates(subject_id="miles") == [
        {
            "id": 1, "kind": "home", "label": None, "saved": False, "lat": 22.3, "lng": 114.2,
            "radius_m": 120.0, "confidence": 0.5,
            "first_seen_ts": 1, "last_seen_ts": 2,
            "stay_count": 1, "total_dwell_ms": 60_000,
        },
        {
            "id": 2, "kind": "unlabeled", "label": "Gym", "saved": True, "lat": 22.4,
            "lng": 114.2, "radius_m": 100.0, "confidence": 0.0,
            "first_seen_ts": 3, "last_seen_ts": 4,
            "stay_count": 0, "total_dwell_ms": 0,
        },
    ]
    await store.rhythm_bins(subject_id="miles", window_ms=10**30)
    await store.trips_with_corridors(subject_id="miles", window_ms=10**30)

    for statement in engine.connection.statements:
        assert str(statement).lstrip().upper().startswith("SELECT")
    assert engine.connection.statements[0]._limit_clause.value == 100
    assert engine.connection.statements[2]._limit_clause.value == 5_000
    assert engine.connection.statements[3]._limit_clause.value == 5_000


async def test_stays_for_subject_window_is_select_only_capped_and_carries_latlng(monkeypatch):
    """DEC-155 day arm: one SELECT, clamped, `window_ms=0` answers empty.
    The joined place is kind/label, and DEC-156 adds the stay centroid
    lat/lng — the fetch surface for a street-view/maps tool (never a reply,
    R-28's speech half stands)."""
    start = _ts(2026, 9, 4, 9)
    engine = _Engine([
        _Result([
            SimpleNamespace(
                started_ts=start, ended_ts=start + 3_600_000,
                lat=22.410, lng=114.124,
                place_kind="home", place_label="Home", place_saved_id="l360-home",
            ),
        ]),
        _Result([
            SimpleNamespace(
                started_ts=start, ended_ts=start + 3_600_000,
                lat=22.410, lng=114.124,
                place_kind="unlabeled", place_label=None, place_saved_id=None,
            ),
        ]),
        _Result([]),
    ])
    monkeypatch.setattr(mobility_sql.time, "time", lambda: (start + 86_400_000) / 1000)
    store = SqlMobilityStore(engine, build_id=1)

    assert await store.stays_for_subject_window(subject_id="miles", window_ms=0) == []
    assert engine.connection.statements == []

    rows = await store.stays_for_subject_window(
        subject_id="miles", window_ms=24 * 60 * 60 * 1000,
    )
    assert rows == [{
        "started_ts": start, "ended_ts": start + 3_600_000,
        "lat": 22.410, "lng": 114.124,
        "place_kind": "home", "place_label": "Home", "place_saved": True,
    }]

    unlabeled = await store.stays_for_subject_window(
        subject_id="miles", window_ms=24 * 60 * 60 * 1000,
    )
    assert unlabeled[0]["place_kind"] == "unlabeled"
    assert unlabeled[0]["place_label"] is None
    assert unlabeled[0]["place_saved"] is False

    await store.stays_for_subject_window(subject_id="miles", window_ms=10**30)

    for statement in engine.connection.statements:
        assert str(statement).lstrip().upper().startswith("SELECT")
    assert engine.connection.statements[0]._limit_clause.value == 5_000
    assert engine.connection.statements[2]._limit_clause.value == 5_000
