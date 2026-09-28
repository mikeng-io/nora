"""Tier 4 forecasting and anomaly detection (`nora.domain.mobility_forecast`).

The operator's target question is time-anchored: «where will Grant be on
Wednesday night?» — a frequency table over (hour-of-week, place), not a
next-place chain (design/27 §4.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nora.domain.mobility import Stay
from nora.domain.mobility_forecast import (
    backtest_forecast,
    detect_stay_anomalies,
    forecast_window,
)

_HKT = ZoneInfo("Asia/Hong_Kong")
HOME, SECOND_HOME, OFFICE, GYM = 1, 2, 3, 4
_AT = {
    HOME: (22.388, 114.037), SECOND_HOME: (22.410, 114.177),
    OFFICE: (22.376, 114.197), GYM: (22.300, 114.170), None: (22.100, 114.000),
}
MONDAY = datetime(2026, 8, 3, tzinfo=_HKT)  # a Monday


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _stay(day: datetime, start_h: float, end_h: float, place: int | None) -> Stay:
    lat, lng = _AT[place]
    return Stay(
        subject_id="grant", person_id="grant", circle_id="c1",
        started_ts=_ms(day + timedelta(hours=start_h)),
        ended_ts=_ms(day + timedelta(hours=end_h)),
        lat=lat, lng=lng, radius_m=30.0, fix_count=8, place_id=place,
    )


def _grant(weeks: int) -> list[Stay]:
    """Works Mon-Fri 09-18; sleeps at home, EXCEPT Tuesday and Thursday
    nights, which he spends at a second home; gym Wednesday 19-21."""
    stays: list[Stay] = []
    for w in range(weeks):
        for d in range(7):
            day = MONDAY + timedelta(days=7 * w + d)
            night = SECOND_HOME if d in (1, 3) else HOME
            if d < 5:
                stays.append(_stay(day, 9, 18, OFFICE))
            if d == 2:
                stays.append(_stay(day, 19, 21, GYM))
                stays.append(_stay(day, 21.5, 31, night))
            else:
                stays.append(_stay(day, 19 if d < 5 else 12, 31, night))
    return stays


def _wednesday_night(weeks_ahead: int) -> tuple[int, int]:
    wed = MONDAY + timedelta(days=7 * weeks_ahead + 2)
    return _ms(wed + timedelta(hours=22)), _ms(wed + timedelta(hours=30))


# --- forecast ----------------------------------------------------------------


def test_where_will_grant_be_on_wednesday_night():
    stays = _grant(weeks=6)
    start, end = _wednesday_night(weeks_ahead=6)
    fc = forecast_window(
        stays, start_ms=start, end_ms=end, tz_name="Asia/Hong_Kong",
        history_before_ms=start,
    )
    assert fc.status == "ok"
    assert fc.basis == "hour"
    assert fc.support_weeks == 6
    assert fc.chances[0].place_id == HOME
    assert fc.chances[0].probability > 0.95


def test_the_tuesday_night_answer_is_the_second_home():
    stays = _grant(weeks=6)
    tue = MONDAY + timedelta(days=7 * 6 + 1)
    fc = forecast_window(
        stays, start_ms=_ms(tue + timedelta(hours=23)),
        end_ms=_ms(tue + timedelta(hours=29)), tz_name="Asia/Hong_Kong",
        history_before_ms=_ms(tue),
    )
    assert fc.chances[0].place_id == SECOND_HOME


def test_a_forecast_never_reads_its_future():
    # History cut at week 3: a pattern that only starts in week 4 is invisible.
    stays = _grant(weeks=3) + [
        _stay(MONDAY + timedelta(days=7 * w + 2), 22, 30, GYM) for w in range(3, 6)
    ]
    start, end = _wednesday_night(weeks_ahead=3)
    fc = forecast_window(
        stays, start_ms=start, end_ms=end, tz_name="Asia/Hong_Kong",
        history_before_ms=start,
    )
    assert fc.chances[0].place_id == HOME


def test_recent_weeks_outweigh_old_ones_after_a_move():
    # Four weeks at the old place, then three at the new one: recency wins.
    old = [_stay(MONDAY + timedelta(days=7 * w + 2), 22, 30, HOME) for w in range(4)]
    new = [
        _stay(MONDAY + timedelta(days=7 * w + 2), 22, 30, SECOND_HOME)
        for w in range(4, 7)
    ]
    start, end = _wednesday_night(weeks_ahead=7)
    fc = forecast_window(
        old + new, start_ms=start, end_ms=end, tz_name="Asia/Hong_Kong",
        history_before_ms=start, half_life_days=14,
    )
    assert fc.chances[0].place_id == SECOND_HOME


def test_thin_history_falls_back_and_says_so():
    # One week of history cannot support the hour itself (needs 3 weeks):
    # the answer comes from a weaker level, and the level is reported.
    stays = _grant(weeks=1)
    start, end = _wednesday_night(weeks_ahead=1)
    fc = forecast_window(
        stays, start_ms=start, end_ms=end, tz_name="Asia/Hong_Kong",
        history_before_ms=start,
    )
    assert fc.status == "ok"
    assert fc.basis == "overall"
    assert fc.support_weeks < 3  # below the 3-week bar the hour level needs


def test_no_history_is_insufficient_not_a_guess():
    start, end = _wednesday_night(weeks_ahead=0)
    fc = forecast_window(
        [], start_ms=start, end_ms=end, tz_name="Asia/Hong_Kong",
        history_before_ms=start,
    )
    assert (fc.status, fc.chances, fc.basis) == ("insufficient", (), "none")


def test_the_backtest_beats_the_trivial_baseline_on_a_real_pattern():
    # The committed instrument design/27 §4.1 asked for: rolling origin, one
    # day at a time, history strictly before each test day. On a subject with
    # a genuine weekly structure it must beat «always their usual place».
    stays = _grant(weeks=8)
    result = backtest_forecast(
        stays, tz_name="Asia/Hong_Kong", start_ms=_ms(MONDAY),
        end_ms=_ms(MONDAY + timedelta(days=56)),
    )
    assert result.bins > 500
    assert result.accuracy is not None and result.baseline_accuracy is not None
    assert result.accuracy > result.baseline_accuracy + 0.3
    # The first two weeks are never scored (min_history_days=14).
    assert min(d.day_start_ms for d in result.days) >= _ms(MONDAY + timedelta(days=14))


# --- anomalies ---------------------------------------------------------------


def _detect(stay, history):
    return detect_stay_anomalies(
        stay, history=history, tz_name="Asia/Hong_Kong", min_samples=5,
        z_threshold=3.0, novelty_radius_m=100.0,
    )


def test_still_at_the_office_at_23_00_is_a_departure_anomaly():
    history = _grant(weeks=4)
    late = _stay(MONDAY + timedelta(days=28), 9, 23, OFFICE)
    kinds = {a.kind: a for a in _detect(late, history)}
    assert "departure" in kinds and kinds["departure"].score > 3
    assert "duration" in kinds


def test_an_ordinary_day_is_not_anomalous():
    history = _grant(weeks=4)
    ordinary = _stay(MONDAY + timedelta(days=28), 9, 18.2, OFFICE)
    assert _detect(ordinary, history) == []


def test_cadence_noise_is_never_an_anomaly():
    # design/27 §1.3: a baseline agreeing to within seconds (MAD ~0) used to
    # make a two-second deviation |z| >= 3. The MAD floor stops it.
    history = [_stay(MONDAY + timedelta(days=d), 9, 18, OFFICE) for d in range(10)]
    wobble = _stay(MONDAY + timedelta(days=10), 9, 18 + 5 / 3600, OFFICE)
    assert _detect(wobble, history) == []


def test_below_min_samples_nothing_is_claimed():
    history = [_stay(MONDAY + timedelta(days=d), 9, 18, OFFICE) for d in range(3)]
    assert _detect(_stay(MONDAY + timedelta(days=3), 9, 23, OFFICE), history) == []


def test_a_never_visited_place_is_new():
    history = _grant(weeks=3)
    somewhere = _stay(MONDAY + timedelta(days=21, hours=0), 13, 15, None)
    assert [a.kind for a in _detect(somewhere, history)] == ["new_place"]


def test_not_at_home_at_the_hour_he_always_is_is_unusual():
    history = _grant(weeks=6)
    wed = MONDAY + timedelta(days=7 * 6 + 2)
    away = _stay(wed, 22, 30, GYM)
    found = [a for a in _detect(away, history) if a.kind == "unusual_place"]
    assert len(found) == 1
    assert found[0].expected_place_id == HOME
    assert found[0].score > 0.8


# --- the movement model (design/27 §4.4) -------------------------------------

from nora.domain.mobility_forecast import (  # noqa: E402
    anchor_events,
    habits_of,
    time_forecast,
    trip_patterns,
)


def _wednesday_nights(weeks: int, late_weeks: set[int]) -> list[Stay]:
    """Office 09-18, then home: back 19:25 most Wednesdays, ~23:45 on the
    `late_weeks` (a gym and dinner first)."""
    stays = []
    for w in range(weeks):
        wed = MONDAY + timedelta(days=7 * w + 2)
        stays.append(_stay(wed, 9, 18, OFFICE))
        if w in late_weeks:
            stays.append(_stay(wed, 19, 23.5, GYM))
            stays.append(_stay(wed, 23 + 45 / 60, 31, HOME))
        else:
            stays.append(_stay(wed, 19 + 25 / 60, 31, HOME))
        stays.append(_stay(wed + timedelta(days=1), 9, 18, OFFICE))
    return stays


def test_arrivals_and_departures_come_from_consecutive_stays():
    stays = _wednesday_nights(1, set())
    events = anchor_events(stays, place_ids={HOME}, tz_name="Asia/Hong_Kong")
    assert [(e.kind, e.minute) for e in events] == [
        ("arrival", 19 * 60 + 25), ("departure", 7 * 60),
    ]


def test_times_are_habits_not_a_median():
    minutes = [1161, 1164, 1165, 1171, 1420, 1425, 1454]
    assert [(h.earliest, h.latest, h.days) for h in habits_of(minutes)] == [
        (1161, 1171, 4), (1420, 1454, 3),
    ]


def test_when_is_he_back_home_on_wednesday_night():
    stays = _wednesday_nights(7, late_weeks={1, 3, 5})
    target = (MONDAY + timedelta(days=7 * 7 + 2)).date()
    fc = time_forecast(
        stays, place_ids={HOME}, kind="arrival", day=target, from_hour=16, to_hour=4,
        tz_name="Asia/Hong_Kong", history_before_ms=_ms(MONDAY + timedelta(days=49)),
    )
    assert (fc.status, fc.basis, fc.days) == ("ok", "weekday", 7)
    assert [(h.earliest, h.days) for h in fc.habits] == [(19 * 60 + 25, 4), (23 * 60 + 45, 3)]


def test_on_nights_like_tonight_only_the_late_ones_count():
    """Asked at 21:00 on the night, not home yet: the four 19:25 Wednesdays
    drop out and the answer is the late habit."""
    stays = _wednesday_nights(7, late_weeks={1, 3, 5})
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    fc = time_forecast(
        stays, place_ids={HOME}, kind="arrival", day=wed.date(), from_hour=16, to_hour=4,
        tz_name="Asia/Hong_Kong", history_before_ms=_ms(wed),
        now_ms=_ms(wed + timedelta(hours=21)), at_place_now=False,
    )
    assert fc.today == "pending"
    assert [(h.earliest, h.days) for h in fc.like_today] == [(23 * 60 + 45, 3)]


def test_already_home_is_done_not_a_forecast():
    stays = _wednesday_nights(7, late_weeks=set())
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    fc = time_forecast(
        stays, place_ids={HOME}, kind="arrival", day=wed.date(), from_hour=16, to_hour=4,
        tz_name="Asia/Hong_Kong", history_before_ms=_ms(wed),
        now_ms=_ms(wed + timedelta(hours=21)), at_place_now=True,
    )
    assert fc.today == "done"


def test_thin_weekday_falls_back_to_the_day_type_and_says_so():
    stays = _wednesday_nights(2, late_weeks=set())  # only 2 Wednesdays
    thursday_evenings = [
        _stay(MONDAY + timedelta(days=7 * w + 3), 19.5, 31, HOME) for w in range(2)
    ]
    target = (MONDAY + timedelta(days=14 + 2)).date()
    fc = time_forecast(
        stays + thursday_evenings, place_ids={HOME}, kind="arrival", day=target,
        from_hour=16, to_hour=4, tz_name="Asia/Hong_Kong",
        history_before_ms=_ms(MONDAY + timedelta(days=14)),
    )
    assert fc.basis == "day_type"


def test_no_events_is_insufficient_not_a_guess():
    fc = time_forecast(
        [], place_ids={HOME}, kind="arrival", day=MONDAY.date(), from_hour=16,
        to_hour=4, tz_name="Asia/Hong_Kong", history_before_ms=_ms(MONDAY),
    )
    assert (fc.status, fc.habits) == ("insufficient", ())


def test_a_repeated_trip_reports_what_it_actually_took():
    from types import SimpleNamespace

    stays, trips = [], []
    for w in range(5):
        day = MONDAY + timedelta(days=7 * w)
        stays.append(_stay(day, 9, 18, OFFICE))
        minutes = 35 + w * 2  # 35..43
        stays.append(_stay(day, 18 + minutes / 60, 30, HOME))
        trips.append(SimpleNamespace(
            started_ts=_ms(day + timedelta(hours=18)),
            ended_ts=_ms(day + timedelta(hours=18, minutes=minutes)),
        ))
    [p] = trip_patterns(stays, trips, tz_name="Asia/Hong_Kong")
    assert (p.origin_id, p.destination_id, p.day_type, p.time_of_day, p.trips) == (
        OFFICE, HOME, "weekday", "evening", 5,
    )
    assert (p.usual_min_minutes, p.usual_max_minutes, p.median_minutes) == (37, 41, 39)
    # Two trips are not a pattern.
    assert trip_patterns(stays[:4], trips[:2], tz_name="Asia/Hong_Kong") == []
