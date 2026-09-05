from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nora.domain.mobility import hour_of_week
from nora.infra.sql import mobility as mobility_sql
from nora.infra.sql.mobility import SqlMobilityStore


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

    bins = await SqlMobilityStore(engine).rhythm_bins(
        subject_id="miles", window_ms=24 * 60 * 60 * 1000,
    )

    assert bins == [{"hour_of_week": hour_of_week(start, "Asia/Hong_Kong"),
                     "stay_count": 1, "total_dwell_ms": 30 * 60_000}]


async def test_places_and_routes_reads_are_select_only_and_capped():
    engine = _Engine([
        _Result([
            SimpleNamespace(
                id=1, kind="home", label=None, lat=22.3, lng=114.2,
                radius_m=120.0, confidence=0.5, first_seen_ts=1, last_seen_ts=2,
            ),
        ]),
        _Result([
            SimpleNamespace(
                lat=22.3005, lng=114.2, started_ts=1_000, ended_ts=61_000,
            ),
            SimpleNamespace(
                lat=23.3, lng=114.2, started_ts=2_000, ended_ts=62_000,
            ),
        ]),
        _Result([]),
        _Result([]),
    ])
    store = SqlMobilityStore(engine)

    assert await store.places_with_aggregates(subject_id="miles") == [
        {
            "kind": "home", "label": None, "lat": 22.3, "lng": 114.2,
            "radius_m": 120.0, "confidence": 0.5,
            "first_seen_ts": 1, "last_seen_ts": 2,
            "stay_count": 1, "total_dwell_ms": 60_000,
        },
    ]
    await store.rhythm_bins(subject_id="miles", window_ms=10**30)
    await store.trips_with_corridors(subject_id="miles", window_ms=10**30)

    for statement in engine.connection.statements:
        assert str(statement).lstrip().upper().startswith("SELECT")
    assert engine.connection.statements[0]._limit_clause.value == 100
    assert engine.connection.statements[1]._limit_clause.value == 5_000
    assert engine.connection.statements[2]._limit_clause.value == 5_000


async def test_stays_for_subject_window_is_select_only_capped_and_strips_coordinates(monkeypatch):
    """DEC-155 day arm: one SELECT, clamped, `window_ms=0` answers empty, and
    the joined place is kind/label only — R-28 keeps lat/lng out of SQL."""
    start = _ts(2026, 9, 4, 9)
    engine = _Engine([
        _Result([
            SimpleNamespace(
                started_ts=start, ended_ts=start + 3_600_000,
                place_kind="home", place_label="Home",
            ),
        ]),
        _Result([
            SimpleNamespace(
                started_ts=start, ended_ts=start + 3_600_000,
                place_kind="unlabeled", place_label=None,
            ),
        ]),
        _Result([]),
    ])
    monkeypatch.setattr(mobility_sql.time, "time", lambda: (start + 86_400_000) / 1000)
    store = SqlMobilityStore(engine)

    assert await store.stays_for_subject_window(subject_id="miles", window_ms=0) == []
    assert engine.connection.statements == []

    rows = await store.stays_for_subject_window(
        subject_id="miles", window_ms=24 * 60 * 60 * 1000,
    )
    assert rows == [{
        "started_ts": start, "ended_ts": start + 3_600_000,
        "place_kind": "home", "place_label": "Home",
    }]
    assert "lat" not in rows[0] and "lng" not in rows[0]

    unlabeled = await store.stays_for_subject_window(
        subject_id="miles", window_ms=24 * 60 * 60 * 1000,
    )
    assert unlabeled[0]["place_kind"] == "unlabeled"
    assert unlabeled[0]["place_label"] is None

    await store.stays_for_subject_window(subject_id="miles", window_ms=10**30)

    for statement in engine.connection.statements:
        assert str(statement).lstrip().upper().startswith("SELECT")
    assert engine.connection.statements[0]._limit_clause.value == 5_000
    assert engine.connection.statements[2]._limit_clause.value == 5_000
