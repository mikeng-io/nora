"""Tier 4 forecasting and Tier 3 anomaly detection over derived stays — pure,
standard library only, under the same neutrality contract as
`nora.domain.mobility` (`pyproject.toml`'s import-linter contract names both
modules).

**The forecaster is a frequency table, not a model.** The question it answers
is TIME-ANCHORED — «at this hour on this weekday, where» — so it is dwell per
(subject, hour-of-week, place), recency-weighted, and one stay contributes to
every hour it spans. It never claims more than its evidence: each answer carries how many past weeks
support it, how much of the hour those weeks actually observed, and which
fallback level produced it, and a subject with no history gets
`insufficient`, never a guess (R-27's third state).

**Anomalies are scored against the subject's OWN history at the SAME place**
(«still at work at 23:00, p99 for her»), robust (median/MAD, never
mean/stddev) and with a MAD floor per quantity so a baseline that agrees to
within seconds cannot make seconds anomalous. Only stays that
closed THIS cycle are scored, against stays that started before them — a stay
is never anomalous relative to itself or to its future.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal
from zoneinfo import ZoneInfo

from nora.domain.mobility import (
    Stay,
    circular_mean_minute,
    robust_z_score,
)
from nora.domain.presence import haversine_distance_m

__all__ = [
    "ARRIVAL_MIN_MAD_MINUTES",
    "DURATION_MIN_MAD_MS",
    "HABIT_GAP_MINUTES",
    "AnchorEvent",
    "Anomaly",
    "ArrivalBacktest",
    "BacktestDay",
    "BacktestResult",
    "Forecast",
    "Habit",
    "PlaceChance",
    "TimeForecast",
    "TripPattern",
    "anchor_events",
    "backtest_arrivals",
    "backtest_forecast",
    "detect_stay_anomalies",
    "forecast_hour",
    "forecast_window",
    "habits_of",
    "place_words",
    "rollover_minute",
    "time_forecast",
    "trip_patterns",
    "window_minute",
]

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_WEEK_MS = 7 * _DAY_MS

# The smallest duration deviation that could mean anything: one stay's
# duration is measured to the resting cadence (~30 min between fixes), so a MAD
# below that is the sampler agreeing with itself.
DURATION_MIN_MAD_MS = 30 * 60_000
# Arrival/departure clock times carry the same cadence error at each end.
ARRIVAL_MIN_MAD_MINUTES = 30.0

# A past week counts as SUPPORT for an hour-of-week only if its stays
# observed at least this much of that hour. Less is a glimpse, not evidence.
_MIN_OBSERVED_MS = 15 * 60_000


# --- hour walking ------------------------------------------------------------


def _hours(started_ts: int, ended_ts: int, tz: ZoneInfo) -> Iterable[tuple[int, int, int]]:
    """`(local_hour_start_ms, hour_of_week, dwell_ms)` for every local clock
    hour a span covers — `hour_of_week_bins`' walk, keeping each hour's own
    start so a caller can tell one week's Wednesday 22:00 from another's."""
    cursor = started_ts
    while cursor < ended_ts:
        local = datetime.fromtimestamp(cursor / 1000, tz=tz)
        hour_start = local.replace(minute=0, second=0, microsecond=0)
        hour_start_ms = int(hour_start.timestamp() * 1000)
        hour_end_ms = hour_start_ms + _HOUR_MS
        yield (
            hour_start_ms,
            local.weekday() * 24 + local.hour,
            min(hour_end_ms, ended_ts) - cursor,
        )
        cursor = hour_end_ms


# --- forecasting -------------------------------------------------------------


@dataclass(frozen=True)
class PlaceChance:
    """One candidate: a derived place id, or `None` for «somewhere that is
    not one of their places»."""

    place_id: int | None
    probability: float


@dataclass(frozen=True)
class Forecast:
    """Where a subject is likely to be across a span of local time.

    `status`: `ok`, or `insufficient` — no history at all to speak from.
    `basis`: which evidence level answered — `hour` (the same hours of the
    week, the frequency table proper), `nearby_hours` (the same weekday one
    hour either side), `day_type` (the same hours on every weekday, or on the
    weekend), `overall` (their all-hours split — a prior, the weakest claim).
    `support_weeks`: distinct past weeks that observed the answering hours.
    `observed_share`: the share of those hours their stays actually covered —
    the rest was travel or no signal, and the probabilities are conditional
    on being stopped somewhere."""

    status: Literal["ok", "insufficient"]
    chances: tuple[PlaceChance, ...]
    basis: Literal["hour", "nearby_hours", "day_type", "overall", "none"]
    support_weeks: int
    observed_share: float


@dataclass(frozen=True)
class _Evidence:
    dwell: dict[int | None, float]  # recency-weighted dwell per place
    weeks: set[int]                 # week indices with >= _MIN_OBSERVED_MS
    observed_ms: int                # unweighted observed dwell
    slots: int                      # hour-slots considered


def _week_index(hour_start_ms: int, tz: ZoneInfo) -> int:
    local = datetime.fromtimestamp(hour_start_ms / 1000, tz=tz)
    monday = local.date() - timedelta(days=local.weekday())
    return monday.toordinal()


def _collect(
    table: dict[int, list[tuple[int, int | None, int]]], hows: Iterable[int], *,
    at_ms: int, half_life_days: float, tz: ZoneInfo,
) -> _Evidence:
    dwell: dict[int | None, float] = {}
    per_slot_week: dict[tuple[int, int], int] = {}
    observed = 0
    for how in hows:
        for hour_start_ms, place_id, ms in table.get(how, ()):
            age_days = max(0.0, (at_ms - hour_start_ms) / _DAY_MS)
            weight = 0.5 ** (age_days / half_life_days)
            dwell[place_id] = dwell.get(place_id, 0.0) + ms * weight
            observed += ms
            key = (how, _week_index(hour_start_ms, tz))
            per_slot_week[key] = per_slot_week.get(key, 0) + ms
    weeks = {week for (_how, week), ms in per_slot_week.items() if ms >= _MIN_OBSERVED_MS}
    return _Evidence(
        dwell=dwell, weeks=weeks, observed_ms=observed,
        slots=len(per_slot_week),
    )


def _history_table(
    stays: Iterable[Stay], *, before_ms: int, tz: ZoneInfo,
) -> dict[int, list[tuple[int, int | None, int]]]:
    """hour_of_week -> [(hour_start_ms, place_id, dwell_ms)] over every stay
    hour that ENDED before `before_ms` — a forecast never reads its future."""
    table: dict[int, list[tuple[int, int | None, int]]] = {}
    for stay in stays:
        end = min(stay.ended_ts, before_ms)
        if end <= stay.started_ts:
            continue
        for hour_start_ms, how, ms in _hours(stay.started_ts, end, tz):
            table.setdefault(how, []).append((hour_start_ms, stay.place_id, ms))
    return table


def _chances(dwell: dict[int | None, float]) -> tuple[PlaceChance, ...]:
    total = sum(dwell.values())
    ranked = sorted(
        dwell.items(),
        key=lambda kv: (-kv[1], kv[0] is None, kv[0] if kv[0] is not None else 0),
    )
    return tuple(PlaceChance(pid, w / total) for pid, w in ranked if w > 0)


def _forecast_from_table(
    table: dict[int, list[tuple[int, int | None, int]]], hows: list[int], *,
    at_ms: int, tz: ZoneInfo, half_life_days: float, min_support_weeks: int,
) -> Forecast:
    if not table:
        return Forecast("insufficient", (), "none", 0, 0.0)
    target = sorted(set(hows))

    def nearby(h: int) -> list[int]:
        day = h // 24
        return [day * 24 + hh for hh in (h % 24 - 1, h % 24, h % 24 + 1) if 0 <= hh < 24]

    def day_type(h: int) -> list[int]:
        days = range(5) if h // 24 < 5 else range(5, 7)
        return [d * 24 + h % 24 for d in days]

    levels: list[tuple[Literal["hour", "nearby_hours", "day_type", "overall"], list[int]]] = [
        ("hour", target),
        ("nearby_hours", sorted({x for h in target for x in nearby(h)})),
        ("day_type", sorted({x for h in target for x in day_type(h)})),
        ("overall", list(range(168))),
    ]
    for basis, level_hows in levels:
        ev = _collect(table, level_hows, at_ms=at_ms, half_life_days=half_life_days, tz=tz)
        if not ev.dwell:
            continue
        if basis != "overall" and len(ev.weeks) < min_support_weeks:
            continue
        # observed_share: of the hour-slots these weeks could have observed,
        # how much their stays covered.
        possible = max(1, len(level_hows) * max(1, len(ev.weeks))) * _HOUR_MS
        return Forecast(
            "ok", _chances(ev.dwell), basis, len(ev.weeks),
            min(1.0, ev.observed_ms / possible),
        )
    return Forecast("insufficient", (), "none", 0, 0.0)


def forecast_window(
    stays: Iterable[Stay], *, start_ms: int, end_ms: int, tz_name: str,
    history_before_ms: int, half_life_days: float = 28.0,
    min_support_weeks: int = 3,
) -> Forecast:
    """Where the subject is likely to be across `[start_ms, end_ms)` local
    time — e.g. «Wednesday night» as Wednesday 20:00 to Thursday 06:00 —
    from stays that ended before `history_before_ms` (normally now).

    Evidence levels, strongest first, and the first with `min_support_weeks`
    past weeks of observation answers: the same hours of the week; the same
    weekday one hour either side; the same hours on every weekday (or every
    weekend day); their overall split. Recent weeks weigh more (half-life
    `half_life_days`) so a move of house is followed within weeks, not
    averaged against a year of the old address."""
    tz = ZoneInfo(tz_name)
    if end_ms <= start_ms:
        return Forecast("insufficient", (), "none", 0, 0.0)
    hows = [how for _hs, how, _ms in _hours(start_ms, end_ms, tz)]
    table = _history_table(stays, before_ms=history_before_ms, tz=tz)
    return _forecast_from_table(
        table, hows, at_ms=start_ms, tz=tz, half_life_days=half_life_days,
        min_support_weeks=min_support_weeks,
    )


def forecast_hour(
    stays: Iterable[Stay], *, at_ms: int, tz_name: str, history_before_ms: int,
    half_life_days: float = 28.0, min_support_weeks: int = 3,
) -> Forecast:
    """`forecast_window` for the one local clock hour containing `at_ms`."""
    return forecast_window(
        stays, start_ms=at_ms, end_ms=at_ms + 1, tz_name=tz_name,
        history_before_ms=history_before_ms, half_life_days=half_life_days,
        min_support_weeks=min_support_weeks,
    )


# --- the committed instrument -----------------------------------------------


@dataclass(frozen=True)
class BacktestDay:
    day_start_ms: int
    bins: int
    hits: int
    baseline_hits: int


@dataclass(frozen=True)
class BacktestResult:
    """A rolling-origin evaluation: for every test day, the forecaster sees
    only stays that ended before that day began, predicts each local hour's
    dominant place, and is scored against the place the stays actually
    covered most in that hour. `baseline_hits` is the same bins scored for
    «always the place they spend most time», the trivial forecaster this must
    beat to be worth anything."""

    days: tuple[BacktestDay, ...]

    @property
    def bins(self) -> int:
        return sum(d.bins for d in self.days)

    @property
    def accuracy(self) -> float | None:
        return None if not self.bins else sum(d.hits for d in self.days) / self.bins

    @property
    def baseline_accuracy(self) -> float | None:
        return None if not self.bins else sum(d.baseline_hits for d in self.days) / self.bins


def backtest_forecast(
    stays: list[Stay], *, tz_name: str, start_ms: int, end_ms: int,
    min_history_days: int = 14, min_observed_ms: int = 30 * 60_000,
    half_life_days: float = 28.0, min_support_weeks: int = 3,
) -> BacktestResult:
    """Rolling-origin backtest over `[start_ms, end_ms)`, one local day at a
    time; a day with less than `min_history_days` of history behind it is
    skipped, and an hour the stays observed for less than `min_observed_ms`
    is not scored (no ground truth to score against).

    **Known leak, stated:** place identities are the CURRENT ones — the
    reconcile that assigned `place_id` saw every stay, including the test
    day's. A cluster that only exists because of later visits is therefore
    visible to an earlier prediction. It flatters the forecaster slightly on
    new places and not at all on established ones; report it with any number
    this produces."""
    tz = ZoneInfo(tz_name)
    ordered = sorted(stays, key=lambda s: s.started_ts)
    if not ordered:
        return BacktestResult(())
    first_ts = ordered[0].started_ts
    day = datetime.fromtimestamp(start_ms / 1000, tz=tz).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    days: list[BacktestDay] = []
    while int(day.timestamp() * 1000) < end_ms:
        day_start = int(day.timestamp() * 1000)
        next_day = day + timedelta(days=1)
        day_end = int(next_day.timestamp() * 1000)
        day = next_day
        if day_start - first_ts < min_history_days * _DAY_MS:
            continue
        history = [s for s in ordered if s.ended_ts <= day_start]
        if not history:
            continue
        table = _history_table(history, before_ms=day_start, tz=tz)
        overall: dict[int | None, int] = {}
        for s in history:
            overall[s.place_id] = overall.get(s.place_id, 0) + (s.ended_ts - s.started_ts)
        baseline = max(
            overall.items(),
            key=lambda kv: (kv[1], kv[0] is not None, -(kv[0] or 0)),
        )[0]
        truth: dict[int, dict[int | None, int]] = {}
        for s in ordered:
            if s.ended_ts <= day_start or s.started_ts >= day_end:
                continue
            for hour_start_ms, _how, ms in _hours(
                max(s.started_ts, day_start), min(s.ended_ts, day_end), tz,
            ):
                bucket = truth.setdefault(hour_start_ms, {})
                bucket[s.place_id] = bucket.get(s.place_id, 0) + ms
        bins = hits = baseline_hits = 0
        for hour_start_ms, by_place in sorted(truth.items()):
            if sum(by_place.values()) < min_observed_ms:
                continue
            actual = max(by_place.items(), key=lambda kv: kv[1])[0]
            local = datetime.fromtimestamp(hour_start_ms / 1000, tz=tz)
            predicted = _forecast_from_table(
                table, [local.weekday() * 24 + local.hour], at_ms=hour_start_ms,
                tz=tz, half_life_days=half_life_days,
                min_support_weeks=min_support_weeks,
            )
            bins += 1
            if predicted.chances and predicted.chances[0].place_id == actual:
                hits += 1
            if baseline == actual:
                baseline_hits += 1
        if bins:
            days.append(BacktestDay(day_start, bins, hits, baseline_hits))
    return BacktestResult(tuple(days))


# --- anomalies ---------------------------------------------------------------


AnomalyKind = Literal["duration", "arrival", "departure", "new_place", "unusual_place"]


@dataclass(frozen=True)
class Anomaly:
    """One unusual thing about one closed stay. `observed`/`expected` are in
    the kind's own unit — milliseconds for `duration`, local minute-of-day
    for `arrival`/`departure` — and `score` is the robust z (signed: positive
    is longer/later). `new_place` and `unusual_place` carry no z: their
    `score` is how confident the expectation they broke was (1.0 for a place
    never visited before; the forecast's top probability for
    `unusual_place`), and `expected_place_id` is where they usually are."""

    subject_id: str
    stay_started_ts: int
    place_id: int | None
    kind: AnomalyKind
    observed: float | None
    expected: float | None
    score: float
    expected_place_id: int | None = None


def _minute_of_day(ts: int, tz: ZoneInfo) -> float:
    local = datetime.fromtimestamp(ts / 1000, tz=tz)
    return local.hour * 60.0 + local.minute + local.second / 60.0


def _wrap(delta: float) -> float:
    """A signed minute difference on the 24 h circle, in [-720, 720)."""
    return (delta + 720.0) % 1440.0 - 720.0


def _clock_z(value: float, history: list[float], *, min_samples: int) -> tuple[float, float] | None:
    center = circular_mean_minute(history)
    z = robust_z_score(
        _wrap(value - center), [_wrap(h - center) for h in history],
        min_samples=min_samples, min_mad=ARRIVAL_MIN_MAD_MINUTES,
    )
    return None if z is None else (z, center)


def detect_stay_anomalies(
    stay: Stay, *, history: list[Stay], tz_name: str, min_samples: int,
    z_threshold: float, novelty_radius_m: float,
    novelty_min_dwell_ms: int = 45 * 60_000,
    novelty_min_history_days: int = 14,
    unusual_min_probability: float = 0.8, unusual_min_support_weeks: int = 4,
    unusual_min_dwell_ms: int = 60 * 60_000,
) -> list[Anomaly]:
    """Everything unusual about ONE closed, linked stay, against `history` —
    the subject's other stays (the caller passes all of them; only those that
    started before `stay` are read).

    - `duration`, `arrival`, `departure`: robust z against the prior stays
      at the SAME place (`stay.place_id`), fail-closed below `min_samples`,
      MAD floored so cadence noise is never an anomaly. Unplaced stays have
      no baseline and are never scored on these.
    - `new_place`: a stay of at least `novelty_min_dwell_ms` where no prior
      stay ever came within `novelty_radius_m`, for a subject with at least
      `novelty_min_history_days` of history (in week one, everywhere is new).
    - `unusual_place`: the forecast for this stay's hours, built only from
      earlier stays, put them somewhere else with at least
      `unusual_min_probability` on `unusual_min_support_weeks` weeks of the
      same hours — «usually home at this hour, and wasn't»."""
    tz = ZoneInfo(tz_name)
    prior = [s for s in history if s.started_ts < stay.started_ts]
    out: list[Anomaly] = []

    def add(kind: AnomalyKind, observed: float | None, expected: float | None,
            score: float, expected_place_id: int | None = None) -> None:
        out.append(Anomaly(
            subject_id=stay.subject_id, stay_started_ts=stay.started_ts,
            place_id=stay.place_id, kind=kind, observed=observed,
            expected=expected, score=score, expected_place_id=expected_place_id,
        ))

    if stay.place_id is not None:
        same = [s for s in prior if s.place_id == stay.place_id]
        durations = [float(s.ended_ts - s.started_ts) for s in same]
        duration = float(stay.ended_ts - stay.started_ts)
        z = robust_z_score(
            duration, durations, min_samples=min_samples,
            min_mad=DURATION_MIN_MAD_MS,
        )
        if z is not None and abs(z) >= z_threshold:
            ordered = sorted(durations)
            n = len(ordered)
            median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
            add("duration", duration, median, z)
        for kind, ts, attr in (
            ("arrival", stay.started_ts, "started_ts"),
            ("departure", stay.ended_ts, "ended_ts"),
        ):
            scored = _clock_z(
                _minute_of_day(ts, tz),
                [_minute_of_day(getattr(s, attr), tz) for s in same],
                min_samples=min_samples,
            )
            if scored is not None and abs(scored[0]) >= z_threshold:
                add(kind, _minute_of_day(ts, tz), scored[1], scored[0])  # type: ignore[arg-type]

    dwell = stay.ended_ts - stay.started_ts
    history_span = (
        stay.started_ts - min(s.started_ts for s in prior) if prior else 0
    )
    if (
        dwell >= novelty_min_dwell_ms
        and history_span >= novelty_min_history_days * _DAY_MS
        and not any(
            haversine_distance_m(stay.lat, stay.lng, s.lat, s.lng) <= novelty_radius_m
            for s in prior
        )
    ):
        add("new_place", None, None, 1.0)

    if dwell >= unusual_min_dwell_ms and prior:
        fc = forecast_window(
            prior, start_ms=stay.started_ts, end_ms=stay.ended_ts, tz_name=tz_name,
            history_before_ms=stay.started_ts,
            min_support_weeks=unusual_min_support_weeks,
        )
        if (
            fc.status == "ok" and fc.basis == "hour" and fc.chances
            and fc.chances[0].probability >= unusual_min_probability
            and fc.chances[0].place_id is not None
            and fc.chances[0].place_id != stay.place_id
        ):
            add(
                "unusual_place", None, None, fc.chances[0].probability,
                expected_place_id=fc.chances[0].place_id,
            )
    return out


#: What a place with no household name is to its person, by its kind.
_UNNAMED_PLACE = {
    "home": "a home of theirs", "work": "their work", "unlabeled": "a place they go",
}


def place_words(label: str | None, kind: str, saved: bool) -> str:
    """What she calls a place: its household name when Life360 saved one;
    otherwise what it is to them, near the street its label gives. An unsaved
    label is the street address of the spot (the geocode cache), not a name, so
    it is never read as one. The one spelling every
    reader of a place uses (the presence tools, a Cognition rollup line)."""
    unnamed = _UNNAMED_PLACE.get(kind, _UNNAMED_PLACE["unlabeled"])
    if label and saved:
        return label
    if label:
        return f"{unnamed} near {label}"
    return unnamed


# --- the movement model -----------------------------------------------------
#
# When people leave and arrive (with «days like today»), and the trips they
# repeat with how long those take. Reported as HABITS, never as one average:
# returns home at 19:21-19:31 on four nights and 23:40-00:14 on three have a
# median, 19:31, that describes neither.

# Up to 04:00 still belongs to the previous day's evening («home at 00:14»
# is Wednesday night's return, not Thursday's).
_ROLLOVER_HOUR = 4
# Two event times more than this far apart are different habits.
HABIT_GAP_MINUTES = 90
# How far back a habit is read; routines move with jobs and seasons.
_HABIT_WEEKS = 12


def rollover_minute(ts: int, tz: ZoneInfo) -> tuple[date, int]:
    """The local day an instant belongs to, and its minute of that day, where
    00:00-03:59 counts as the previous day's 24:00-27:59."""
    local = datetime.fromtimestamp(ts / 1000, tz=tz)
    minute = local.hour * 60 + local.minute
    if local.hour < _ROLLOVER_HOUR:
        return local.date() - timedelta(days=1), minute + 1440
    return local.date(), minute


def _window_bounds(from_hour: int, to_hour: int) -> tuple[int, int]:
    """The asked window as minutes of its own day: past midnight when
    `to_hour <= from_hour`, so 00:14 the next morning is minute 1454."""
    lo, hi = from_hour * 60, to_hour * 60
    if hi <= lo:
        hi += 1440
    return lo, hi


def window_minute(
    ts: int, tz: ZoneInfo, from_hour: int, to_hour: int,
) -> tuple[date, int] | None:
    """The day whose window (`from_hour` to `to_hour`) holds this instant, and
    the minute of that day it falls at; `None` outside every such window.

    The window's own start day, not a fixed rollover hour: a 0-to-23 window
    read against a 04:00 rollover would lose every event after 04:00."""
    lo, hi = _window_bounds(from_hour, to_hour)
    local = datetime.fromtimestamp(ts / 1000, tz=tz)
    minute = local.hour * 60 + local.minute
    for day, m in ((local.date(), minute), (local.date() - timedelta(days=1), minute + 1440)):
        if lo <= m <= hi:
            return day, m
    return None


def _instant_ms(day: date, minute: int, tz: ZoneInfo) -> int:
    """The instant `minute` minutes after `day`'s local midnight."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
    return int((midnight + timedelta(minutes=minute)).timestamp() * 1000)


@dataclass(frozen=True)
class AnchorEvent:
    kind: Literal["arrival", "departure"]
    ts: int
    day: date
    minute: int


def anchor_events(
    stays: Iterable[Stay], *, place_ids: set[int], tz_name: str,
) -> list[AnchorEvent]:
    """Arrivals at and departures from a set of places (one person's home, or
    work), from consecutive stays: an arrival starts a stay there that follows
    a stay elsewhere; a departure ends one there that precedes a stay
    elsewhere. Two stays at the place back to back (a stay split by a gap) are
    neither."""
    tz = ZoneInfo(tz_name)
    ordered = sorted(stays, key=lambda s: s.started_ts)
    out: list[AnchorEvent] = []
    for prev, cur in pairwise(ordered):
        prev_in, cur_in = prev.place_id in place_ids, cur.place_id in place_ids
        if cur_in and not prev_in:
            day, minute = rollover_minute(cur.started_ts, tz)
            out.append(AnchorEvent("arrival", cur.started_ts, day, minute))
        elif prev_in and not cur_in:
            day, minute = rollover_minute(prev.ended_ts, tz)
            out.append(AnchorEvent("departure", prev.ended_ts, day, minute))
    return out


@dataclass(frozen=True)
class Habit:
    """One kind of day: its event times ran from `earliest` to `latest`
    (rollover minutes, so 24:14 is 00:14) on `days` of the days read."""

    earliest: int
    latest: int
    days: int


def habits_of(minutes: Iterable[int], *, gap: int = HABIT_GAP_MINUTES) -> tuple[Habit, ...]:
    """Event minutes split into habits wherever two sorted times are more than
    `gap` apart; most frequent first."""
    ordered = sorted(minutes)
    if not ordered:
        return ()
    groups: list[list[int]] = [[ordered[0]]]
    for m in ordered[1:]:
        if m - groups[-1][-1] > gap:
            groups.append([])
        groups[-1].append(m)
    return tuple(sorted(
        (Habit(g[0], g[-1], len(g)) for g in groups),
        key=lambda h: (-h.days, h.earliest),
    ))


@dataclass(frozen=True)
class TimeForecast:
    """When someone usually arrives at (or leaves) a place on a kind of day.

    `days` counts the past days of the basis on which they were seen at all
    in the window, so a habit reads «on 4 of 7» and the other 3 are days the
    event did not happen in these hours. `basis`: `weekday` (the same
    weekday), `day_type` (every weekday, or every weekend day, when the
    weekday alone has fewer than the minimum), `all_days` (the weakest),
    `none` (no event at all).

    `today`, when `now` falls inside the asked day's window:
    - `done`: it already happened (`done_minute` when the time is known);
    - `pending`: not yet, with `like_today`, the habits of only the past days
      that were in the same state at this minute (not there yet, or still
      there), and `like_today_without`, how many of those
      `like_today_days` never had it happen in the window;
    - `stepped_out` (departure only): they left the place in the window but
      have been away less than the two hours that make a leaving, so they
      may be back (`done_minute` is when they went out);
    - `absent` (departure only): they are not there now and were not there
      earlier in the window, so there is nothing to leave;
    - `unknown`: no fresh reading of where they are, so today is not said
      (R-5: I could not check is not «not yet»)."""

    status: Literal["ok", "insufficient"]
    kind: Literal["arrival", "departure"]
    habits: tuple[Habit, ...]
    days: int
    basis: Literal["weekday", "day_type", "all_days", "none"]
    today: Literal[
        "done", "pending", "stepped_out", "absent", "unknown", "not_today",
    ] = "not_today"
    done_minute: int | None = None
    like_today: tuple[Habit, ...] = ()
    like_today_days: int = 0
    like_today_without: int = 0


# A departure is a leaving only when they stay away this long: an hour out
# for lunch is not «leaving work».
_AWAY_MINUTES = 120
# A closed stay at the place that ended this recently is the same visit as
# the fresh reading there, so its start is when they got in.
_SAME_VISIT_MS = 30 * 60_000


def _day_events(
    stays: list[Stay], *, place_ids: set[int], kind: str, tz: ZoneInfo,
    from_hour: int, to_hour: int,
) -> dict[date, list[int]]:
    """Each window day's event minutes: every arrival, or every departure
    after which they stayed away `_AWAY_MINUTES` or more."""
    events = anchor_events(stays, place_ids=place_ids, tz_name=tz.key)
    returns = sorted(e.ts for e in events if e.kind == "arrival")
    out: dict[date, list[int]] = {}
    for ev in events:
        if ev.kind != kind:
            continue
        if kind == "departure":
            i = bisect.bisect_right(returns, ev.ts)
            if i < len(returns) and returns[i] - ev.ts < _AWAY_MINUTES * 60_000:
                continue
        hit = window_minute(ev.ts, tz, from_hour, to_hour)
        if hit is not None:
            out.setdefault(hit[0], []).append(hit[1])
    return out


def _observed_days(
    stays: list[Stay], *, tz: ZoneInfo, from_hour: int, to_hour: int,
) -> set[date]:
    """The window days on which any stay of theirs overlaps the window."""
    lo, hi = _window_bounds(from_hour, to_hour)
    out: set[date] = set()
    for s in stays:
        first = datetime.fromtimestamp(s.started_ts / 1000, tz=tz).date() - timedelta(days=1)
        last = datetime.fromtimestamp(s.ended_ts / 1000, tz=tz).date()
        d = first
        while d <= last:
            if s.started_ts <= _instant_ms(d, hi, tz) and s.ended_ts >= _instant_ms(d, lo, tz):
                out.add(d)
            d += timedelta(days=1)
    return out


def _at_place(stays: list[Stay], place_ids: set[int], ts: int) -> bool:
    """Whether a stay at the place covers this instant (in transit, or at
    another place, is not)."""
    return any(
        s.started_ts <= ts <= s.ended_ts for s in stays if s.place_id in place_ids
    )


def time_forecast(
    stays: list[Stay], *, place_ids: set[int], kind: Literal["arrival", "departure"],
    day: date, from_hour: int, to_hour: int, tz_name: str, history_before_ms: int,
    now_ms: int | None = None, at_place_now: bool | None = None,
    min_days: int = 3,
) -> TimeForecast:
    """When they usually arrive at / leave `place_ids` within the local window
    `from_hour`..`to_hour` (past midnight when `to_hour <= from_hour`) on days
    like `day`, from stays before `history_before_ms`, the last twelve weeks.

    Per past day, an arrival is the LAST arrival in the window («when is he
    back home tonight» is the final return), a departure the FIRST one after
    which they stayed away two hours («when does he leave work», not lunch).

    `stays` are closed, so where someone is NOW comes only from
    `at_place_now`, the caller's fresh live reading; `None` makes today
    `unknown`. Past days are matched to today by their state at this same
    minute: asked at 21:00 with him not home, only the nights he was not home
    at 21:00 either are read («he got in around midnight»)."""
    tz = ZoneInfo(tz_name)
    oldest = history_before_ms - _HABIT_WEEKS * _WEEK_MS
    history = sorted(
        (s for s in stays if oldest <= s.started_ts and s.ended_ts <= history_before_ms),
        key=lambda s: s.started_ts,
    )
    lo, hi = _window_bounds(from_hour, to_hour)

    def past(d: date) -> bool:
        # A past day is one whose window had CLOSED by the cutoff: asked at
        # 01:00 about tomorrow, tonight's 22-to-06 is still open and partial.
        return d < day and _instant_ms(d, hi, tz) <= history_before_ms

    events = {
        d: ms for d, ms in _day_events(
            history, place_ids=place_ids, kind=kind, tz=tz, from_hour=from_hour,
            to_hour=to_hour,
        ).items() if past(d)
    }
    pick = max if kind == "arrival" else min
    per_day = {d: pick(ms) for d, ms in events.items()}
    observed = {
        d for d in _observed_days(history, tz=tz, from_hour=from_hour, to_hour=to_hour)
        if past(d)
    } | set(per_day)

    weekend = day.weekday() >= 5
    chosen: dict[date, int] = {}
    basis: Literal["weekday", "day_type", "all_days", "none"] = "none"
    in_basis: Any = None
    for level, pred in (
        ("weekday", lambda d: d.weekday() == day.weekday()),
        ("day_type", lambda d: (d.weekday() >= 5) == weekend),
        ("all_days", lambda d: True),
    ):
        candidate = {d: m for d, m in per_day.items() if pred(d)}
        if len(candidate) >= min_days:
            chosen, basis, in_basis = candidate, level, pred  # type: ignore[assignment]
            break
    if not chosen:
        return TimeForecast("insufficient", kind, (), 0, "none")
    basis_days = sorted(d for d in observed if in_basis(d))

    forecast = TimeForecast("ok", kind, habits_of(chosen.values()), len(basis_days), basis)
    if now_ms is None:
        return forecast
    now_hit = window_minute(now_ms, tz, from_hour, to_hour)
    if now_hit is None or now_hit[0] != day:
        return forecast
    now_minute = now_hit[1]
    if at_place_now is None:
        return TimeForecast(
            "ok", kind, forecast.habits, forecast.days, basis, today="unknown",
        )
    window_start = _instant_ms(day, lo, tz)
    closed = sorted((s for s in stays if s.ended_ts <= now_ms), key=lambda s: s.started_ts)

    if kind == "arrival" and at_place_now:
        run: list[Stay] = []
        for s in reversed(closed):
            if s.place_id not in place_ids:
                break
            run.insert(0, s)
        since = None
        if (
            run and now_ms - run[-1].ended_ts <= _SAME_VISIT_MS
            and run[0].started_ts >= window_start
        ):
            hit = window_minute(run[0].started_ts, tz, from_hour, to_hour)
            since = hit[1] if hit is not None and hit[0] == day else None
        return TimeForecast(
            "ok", kind, forecast.habits, forecast.days, basis, today="done",
            done_minute=since,
        )
    if kind == "departure" and not at_place_now:
        left = [
            s.ended_ts for s in closed
            if s.place_id in place_ids and window_start <= s.ended_ts <= now_ms
        ]
        if not left:
            return TimeForecast(
                "ok", kind, forecast.habits, forecast.days, basis, today="absent",
            )
        hit = window_minute(max(left), tz, from_hour, to_hour)
        # The habit counts a leaving only after two hours away; out for less
        # so far is not yet «left» (lunch at 12:30 is not «already left work»).
        return TimeForecast(
            "ok", kind, forecast.habits, forecast.days, basis,
            today="done" if now_ms - max(left) >= _AWAY_MINUTES * 60_000 else "stepped_out",
            done_minute=hit[1] if hit is not None else None,
        )

    # Pending: not there yet (arrival) or still there (departure). Read the
    # past days that were in that same state at this minute.
    matched = [
        d for d in basis_days
        if _at_place(history, place_ids, _instant_ms(d, now_minute, tz)) == at_place_now
    ]
    after = []
    for d in matched:
        later = [m for m in events.get(d, ()) if m > now_minute]
        if later:
            after.append(pick(later))
    return TimeForecast(
        "ok", kind, forecast.habits, forecast.days, basis, today="pending",
        like_today=habits_of(after), like_today_days=len(matched),
        like_today_without=len(matched) - len(after),
    )


# --- M2: the trips they repeat ----------------------------------------------

_BUCKETS = (
    (6, 10, "morning"), (10, 16, "midday"), (16, 21, "evening"),
)


def _bucket(hour: int) -> str:
    return next((name for lo, hi, name in _BUCKETS if lo <= hour < hi), "night")


@dataclass(frozen=True)
class TripPattern:
    """A journey someone repeats: from one of their places to another, on
    weekdays or weekends, at a time of day, with how long it usually takes
    (the middle half of its observed durations, in minutes)."""

    origin_id: int
    destination_id: int
    day_type: Literal["weekday", "weekend"]
    time_of_day: str
    trips: int
    usual_min_minutes: int
    usual_max_minutes: int
    median_minutes: int


def trip_patterns(
    stays: list[Stay], trips: Iterable[Any], *, tz_name: str, min_trips: int = 3,
    link_gap_ms: int = 45 * 60_000,
) -> list[TripPattern]:
    """Each trip's origin and destination are the places of the stays either
    side of it (within `link_gap_ms`); a (origin, destination, day type, time of
    day) seen `min_trips` times is a pattern. Durations are what the trips
    actually took, not an estimate."""
    tz = ZoneInfo(tz_name)
    ordered = sorted(stays, key=lambda s: s.started_ts)
    starts = [s.started_ts for s in ordered]
    groups: dict[tuple[int, int, str, str], list[float]] = {}
    for trip in trips:
        i = bisect.bisect_right(starts, trip.started_ts) - 1
        j = bisect.bisect_left(starts, trip.ended_ts)
        if i < 0 or j >= len(ordered):
            continue
        before, after = ordered[i], ordered[j]
        if (
            before.place_id is None or after.place_id is None
            or before.place_id == after.place_id
            or trip.started_ts - before.ended_ts > link_gap_ms
            or after.started_ts - trip.ended_ts > link_gap_ms
        ):
            continue
        local = datetime.fromtimestamp(trip.started_ts / 1000, tz=tz)
        key = (
            before.place_id, after.place_id,
            "weekend" if local.weekday() >= 5 else "weekday", _bucket(local.hour),
        )
        groups.setdefault(key, []).append((trip.ended_ts - trip.started_ts) / 60_000)
    out: list[TripPattern] = []
    for (o, d, day_type, tod), durations in groups.items():
        if len(durations) < min_trips:
            continue
        ordered_d = sorted(durations)
        n = len(ordered_d)
        # Nearest rank at the same distance from each end, so the middle half
        # is symmetric at every n.
        q1, q3 = ordered_d[round((n - 1) / 4)], ordered_d[n - 1 - round((n - 1) / 4)]
        out.append(TripPattern(
            o, d, day_type, tod, n, round(q1), round(q3), round(ordered_d[n // 2]),  # type: ignore[arg-type]
        ))
    return sorted(out, key=lambda p: -p.trips)


@dataclass(frozen=True)
class ArrivalBacktest:
    """The arrival instrument: for each held-out day with
    an arrival in the window, did it fall inside a predicted habit (± the
    tolerance), against «the median of every earlier day ± 30 min».

    `width_minutes` sums the width of the habit windows that were scored
    (tolerance included), so `mean_width` says how wide a net the hit rate
    was bought with: the baseline's net is always `2 * baseline_band_min`."""

    days: int
    hits: int
    baseline_hits: int
    width_minutes: int = 0

    @property
    def accuracy(self) -> float | None:
        return None if not self.days else self.hits / self.days

    @property
    def baseline_accuracy(self) -> float | None:
        return None if not self.days else self.baseline_hits / self.days

    @property
    def mean_width(self) -> float | None:
        return None if not self.days else self.width_minutes / self.days


def backtest_arrivals(
    stays: list[Stay], *, place_ids: set[int], tz_name: str, start_ms: int,
    end_ms: int, from_hour: int = 16, to_hour: int = 4, tolerance_min: int = 15,
    baseline_band_min: int = 30,
) -> ArrivalBacktest:
    """Rolling origin, one window day at a time: predict from stays that
    ended before that day's window opened, score the day's real last arrival
    in the window. The baseline reads the same stays, so neither side sees
    the day it is scored on."""
    tz = ZoneInfo(tz_name)
    lo, _ = _window_bounds(from_hour, to_hour)
    actual = {
        d: max(ms) for d, ms in _day_events(
            stays, place_ids=place_ids, kind="arrival", tz=tz, from_hour=from_hour,
            to_hour=to_hour,
        ).items()
        if start_ms <= _instant_ms(d, lo, tz) < end_ms
    }
    days = hits = baseline_hits = width = 0
    for day, minute in sorted(actual.items()):
        opens = _instant_ms(day, lo, tz)
        fc = time_forecast(
            stays, place_ids=place_ids, kind="arrival", day=day, from_hour=from_hour,
            to_hour=to_hour, tz_name=tz_name, history_before_ms=opens,
        )
        if fc.status != "ok":
            continue
        earlier = sorted(
            max(ms) for d, ms in _day_events(
                [s for s in stays if s.ended_ts <= opens], place_ids=place_ids,
                kind="arrival", tz=tz, from_hour=from_hour, to_hour=to_hour,
            ).items()
            if d < day
        )
        days += 1
        width += sum(h.latest - h.earliest + 2 * tolerance_min for h in fc.habits)
        if any(
            h.earliest - tolerance_min <= minute <= h.latest + tolerance_min
            for h in fc.habits
        ):
            hits += 1
        if earlier and abs(minute - earlier[len(earlier) // 2]) <= baseline_band_min:
            baseline_hits += 1
    return ArrivalBacktest(days, hits, baseline_hits, width)
