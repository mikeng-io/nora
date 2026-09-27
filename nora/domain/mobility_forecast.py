"""Tier 4 forecasting and Tier 3 anomaly detection over derived stays — pure,
standard library only, under the same neutrality contract as
`nora.domain.mobility` (design/27 §3; `pyproject.toml`'s import-linter
contract names both modules).

**The forecaster is a frequency table, not a model** (design/19 §6, design/27
§4.1, issue #7). The question it answers is TIME-ANCHORED — «at this hour on
this weekday, where» — so it is dwell per (subject, hour-of-week, place),
recency-weighted, and one stay contributes to every hour it spans. It never
claims more than its evidence: each answer carries how many past weeks
support it, how much of the hour those weeks actually observed, and which
fallback level produced it, and a subject with no history gets
`insufficient`, never a guess (R-27's third state).

**Anomalies are scored against the subject's OWN history at the SAME place**
(issue #6: «still at work at 23:00, p99 for her»), robust (median/MAD, never
mean/stddev) and with a MAD floor per quantity so a baseline that agrees to
within seconds cannot make seconds anomalous (design/27 §1.3). Only stays that
closed THIS cycle are scored, against stays that started before them — a stay
is never anomalous relative to itself or to its future.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
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
    "Anomaly",
    "BacktestDay",
    "BacktestResult",
    "Forecast",
    "PlaceChance",
    "backtest_forecast",
    "detect_stay_anomalies",
    "forecast_hour",
    "forecast_window",
]

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_WEEK_MS = 7 * _DAY_MS

# The smallest duration deviation that could mean anything: one stay's
# duration is measured to the resting cadence (~30 min between fixes,
# design/27 §1.1), so a MAD below that is the sampler agreeing with itself.
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


# --- the committed instrument (design/27 §4.1) -----------------------------


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
    «always the place they spend most time» — the trivial forecaster §4.1
    says this must beat to be worth anything."""

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
