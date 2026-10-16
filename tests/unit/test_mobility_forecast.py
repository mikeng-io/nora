"""Tier 4 forecasting and anomaly detection (`nora.domain.mobility_forecast`).

The operator's target question is time-anchored: «where will Grant be on
Wednesday night?» — a frequency table over (hour-of-week, place), not a
next-place chain.
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
    # The committed instrument: rolling origin, one
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
    # A baseline agreeing to within seconds (MAD ~0) would make a two-second
    # deviation |z| >= 3; the MAD floor stops it.
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


# ---- the movement model -------------------------------------

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


def test_the_arrival_backtest_beats_a_median_on_two_habits():
    """Two real habits (19:25 and ~23:45): a median band catches neither kind of
    night well; the habit windows catch both."""
    from nora.domain.mobility_forecast import backtest_arrivals

    stays = _wednesday_nights(10, late_weeks={1, 3, 5, 7, 9})
    result = backtest_arrivals(
        stays, place_ids={HOME}, tz_name="Asia/Hong_Kong",
        start_ms=_ms(MONDAY + timedelta(weeks=4)), end_ms=_ms(MONDAY + timedelta(weeks=10)),
    )
    assert result.days == 6
    assert result.accuracy == 1.0
    assert result.baseline_accuracy is not None and result.baseline_accuracy < 0.7


# ---- movement model edge cases ------------------


def _weekly(weeks: int, weekday: int, segments, *, first=MONDAY) -> list[Stay]:
    """`segments` = [(start_h, end_h, place)] on `weekday` of each week."""
    return [
        _stay(first + timedelta(days=7 * w + weekday), a, b, pid)
        for w in range(weeks) for a, b, pid in segments
    ]


def _fc(stays, *, week, weekday=2, kind="arrival", place=HOME, from_hour=16,
        to_hour=4, now_h=None, at_place_now=None):
    day = MONDAY + timedelta(days=7 * week + weekday)
    return time_forecast(
        stays, place_ids={place}, kind=kind, day=day.date(), from_hour=from_hour,
        to_hour=to_hour, tz_name="Asia/Hong_Kong", history_before_ms=_ms(day),
        now_ms=None if now_h is None else _ms(day + timedelta(hours=now_h)),
        at_place_now=at_place_now,
    )


def test_a_window_starting_before_four_keeps_the_rest_of_the_day():
    """Read against a 04:00 rollover, a 0-to-23 window keeps every event after 04:00."""
    fc = _fc(_wednesday_nights(6, set()), week=6, from_hour=0, to_hour=23)
    assert fc.status == "ok"
    assert [(h.earliest, h.days) for h in fc.habits] == [(19 * 60 + 25, 6)]


def test_a_day_without_the_event_counts_against_the_habit():
    """Three Wednesdays at the gym all night: «on 3 of 6», not «on 3 of 3»."""
    stays = _weekly(3, 2, [(9, 18, OFFICE), (19 + 25 / 60, 31, HOME)]) + _weekly(
        3, 2, [(9, 18, OFFICE), (19, 32, GYM)], first=MONDAY + timedelta(weeks=3),
    )
    fc = _fc(stays, week=6)
    assert (fc.days, [(h.earliest, h.days) for h in fc.habits]) == (6, [(1165, 3)])


def test_the_last_arrival_of_the_night_is_the_one_that_counts():
    stays = _weekly(6, 2, [(9, 18, OFFICE), (19, 20, HOME), (20.2, 22, GYM), (22.5, 31, HOME)])
    assert [(h.earliest, h.days) for h in _fc(stays, week=6).habits] == [(22 * 60 + 30, 6)]


def test_an_hour_out_for_lunch_is_not_leaving_work():
    stays = _weekly(6, 4, [
        (9, 12, OFFICE), (12 + 5 / 60, 12 + 55 / 60, None), (13, 18, OFFICE), (18.75, 31, HOME),
    ])
    fc = _fc(stays, week=6, weekday=4, kind="departure", place=OFFICE, from_hour=10, to_hour=20)
    assert [(h.earliest, h.days) for h in fc.habits] == [(18 * 60, 6)]


def test_the_first_real_departure_is_when_they_leave():
    stays = _weekly(6, 4, [(9, 15, OFFICE), (15.2, 18, GYM), (18.2, 19, OFFICE), (19.5, 31, HOME)])
    fc = _fc(stays, week=6, weekday=4, kind="departure", place=OFFICE, from_hour=10, to_hour=22)
    assert [(h.earliest, h.days) for h in fc.habits] == [(15 * 60, 6)]


def test_without_a_fresh_reading_today_is_unknown_not_pending():
    fc = _fc(_wednesday_nights(7, set()), week=7, now_h=21, at_place_now=None)
    assert fc.today == "unknown" and fc.like_today == ()


def test_not_at_work_and_never_there_today_is_absent_not_left():
    """Home all day: the answer is `absent`, not «already left work»."""
    fc = _fc(_wednesday_nights(7, set()), week=7, kind="departure", place=OFFICE,
             from_hour=16, to_hour=22, now_h=20, at_place_now=False)
    assert fc.today == "absent"


def test_left_work_today_is_done_at_the_time_they_left():
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    stays = [*_wednesday_nights(7, set()), _stay(wed, 9, 18, OFFICE)]
    fc = _fc(stays, week=7, kind="departure", place=OFFICE, from_hour=16, to_hour=22,
             now_h=20.5, at_place_now=False)
    assert (fc.today, fc.done_minute) == ("done", 18 * 60)


def test_out_for_lunch_today_is_stepped_out_not_left():
    """The habit counts a leaving only after two hours away; out since 12:30,
    asked at 12:45, is not «already left work»."""
    fri = MONDAY + timedelta(days=7 * 6 + 4)
    stays = [*_weekly(6, 4, [(9, 18, OFFICE), (18.75, 31, HOME)]), _stay(fri, 9, 12.5, OFFICE)]
    fc = _fc(stays, week=6, weekday=4, kind="departure", place=OFFICE, from_hour=10,
             to_hour=20, now_h=12.75, at_place_now=False)
    assert (fc.today, fc.done_minute) == ("stepped_out", 12 * 60 + 30)


def test_a_window_still_open_is_not_a_past_day():
    """Asked at 01:00 about tomorrow night: tonight's 22-to-06 window is still
    open, and its partial evening (a late 23:15 return) must not count."""
    stays = []
    for i in range(9):
        day = MONDAY + timedelta(days=i)
        stays += [_stay(day, 9, 18, OFFICE), _stay(day, 22.5, 24 + 6.5, HOME)]
    tonight = MONDAY + timedelta(days=9)  # a Wednesday
    stays += [_stay(tonight, 9, 18, OFFICE), _stay(tonight, 22, 23, GYM),
              _stay(tonight, 23.25, 24.5, HOME)]
    now = tonight + timedelta(hours=25)
    fc = time_forecast(
        stays, place_ids={HOME}, kind="arrival", day=(tonight + timedelta(days=1)).date(),
        from_hour=22, to_hour=6, tz_name="Asia/Hong_Kong", history_before_ms=_ms(now),
    )
    assert fc.basis == "day_type"
    assert all(h.latest == 22 * 60 + 30 for h in fc.habits)


def test_the_window_edges_are_inside_it():
    """An arrival exactly at the window's last minute counts, and a window
    from an hour to the same hour is the whole day."""
    stays = _weekly(4, 2, [(9, 18, OFFICE), (20, 31, HOME)])
    edge = _fc(stays, week=4, from_hour=16, to_hour=20)
    assert [(h.earliest, h.days) for h in edge.habits] == [(20 * 60, 4)]
    whole = _fc(stays, week=4, from_hour=9, to_hour=9)
    assert [(h.earliest, h.days) for h in whole.habits] == [(20 * 60, 4)]


def test_home_since_yesterday_does_not_date_this_visit_from_last_night():
    """Home since last night, asked at 21:00 inside tonight's window: they are
    home, but not «since» a time in tonight's window."""
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    today = [_stay(wed - timedelta(days=1), 19, 24 + 20.9, HOME)]
    fc = _fc(_wednesday_nights(7, set()) + today, week=7, now_h=21, at_place_now=True)
    assert (fc.today, fc.done_minute) == ("done", None)



def test_home_again_after_going_out_does_not_claim_the_first_arrival():
    """Home 19:00-20:00, out, back by 20:40 (an open stay): «since 19:00» would be
    wrong, and the time of the second arrival is not in closed stays."""
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    today = [_stay(wed, 9, 18, OFFICE), _stay(wed, 19, 20, HOME), _stay(wed, 20.1, 20.5, GYM)]
    fc = _fc(_wednesday_nights(7, set()) + today, week=7, now_h=20 + 40 / 60, at_place_now=True)
    assert (fc.today, fc.done_minute) == ("done", None)


def test_home_since_a_split_stay_says_when_they_got_in():
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    today = [_stay(wed, 9, 18, OFFICE), _stay(wed, 19, 20.5, HOME)]
    fc = _fc(_wednesday_nights(7, set()) + today, week=7, now_h=20 + 40 / 60, at_place_now=True)
    assert (fc.today, fc.done_minute) == ("done", 19 * 60)


def test_days_like_today_are_matched_by_where_they_were_at_this_time():
    """Home by 21:00 every Wednesday, three of them popping out 22-23: asked at
    21:00 with him not home, no past Wednesday is like tonight."""
    stays = []
    for w in range(7):
        wed = MONDAY + timedelta(days=7 * w + 2)
        stays.append(_stay(wed, 9, 18, OFFICE))
        if w in (1, 3, 5):
            stays += [_stay(wed, 19 + 25 / 60, 22, HOME), _stay(wed, 22.05, 23, GYM),
                      _stay(wed, 23.25, 31, HOME)]
        else:
            stays.append(_stay(wed, 19 + 25 / 60, 31, HOME))
    fc = _fc(stays, week=7, now_h=21, at_place_now=False)
    assert (fc.today, fc.like_today_days, fc.like_today) == ("pending", 0, ())


def test_like_today_counts_the_nights_it_never_happened():
    """Not home at 21:00 on three nights: back ~23:45 on two, not at all on one."""
    stays = _wednesday_nights(7, late_weeks={1, 3})
    fifth = MONDAY + timedelta(days=7 * 5 + 2)
    stays = [s for s in stays if not (s.started_ts >= _ms(fifth + timedelta(hours=19))
                                      and s.started_ts < _ms(fifth + timedelta(hours=28)))]
    stays.append(_stay(fifth, 19, 30, GYM))
    fc = _fc(stays, week=7, now_h=21, at_place_now=False)
    assert (fc.like_today_days, fc.like_today_without) == (3, 1)
    assert [(h.earliest, h.days) for h in fc.like_today] == [(23 * 60 + 45, 2)]


def test_the_arrival_backtest_cannot_see_the_day_it_scores():
    """Early for four weeks, then late: the first late night must be a miss
    for both sides. Any leak of later weeks into the history turns it into a
    hit."""
    from nora.domain.mobility_forecast import backtest_arrivals

    stays = _wednesday_nights(8, late_weeks={4, 5, 6, 7})
    result = backtest_arrivals(
        stays, place_ids={HOME}, tz_name="Asia/Hong_Kong",
        start_ms=_ms(MONDAY + timedelta(weeks=3)), end_ms=_ms(MONDAY + timedelta(weeks=8)),
    )
    assert (result.days, result.hits, result.baseline_hits) == (5, 4, 1)
    assert result.mean_width is not None and result.mean_width >= 30


def test_the_middle_half_is_symmetric_at_four_trips():
    from types import SimpleNamespace

    stays, trips = [], []
    for w, minutes in enumerate((30, 32, 34, 50)):
        day = MONDAY + timedelta(days=7 * w)
        stays += [_stay(day, 9, 18, OFFICE), _stay(day, 18 + minutes / 60, 30, HOME)]
        trips.append(SimpleNamespace(
            started_ts=_ms(day + timedelta(hours=18)),
            ended_ts=_ms(day + timedelta(hours=18, minutes=minutes)),
        ))
    [p] = trip_patterns(stays, trips, tz_name="Asia/Hong_Kong")
    assert (p.usual_min_minutes, p.usual_max_minutes) == (32, 34)


def test_a_home_stay_that_ended_long_ago_does_not_date_this_visit():
    """Home 19:00-19:30 on record, nothing since, home again at 20:40: they may
    have gone out in between, so when they got in is not known."""
    wed = MONDAY + timedelta(days=7 * 7 + 2)
    today = [_stay(wed, 9, 18, OFFICE), _stay(wed, 19, 19.5, HOME)]
    fc = _fc(_wednesday_nights(7, set()) + today, week=7, now_h=20 + 40 / 60, at_place_now=True)
    assert (fc.today, fc.done_minute) == ("done", None)
