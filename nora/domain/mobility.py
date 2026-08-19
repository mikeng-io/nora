"""Pure mobility derivation — stay segmentation, place clustering, labelling
(docs/superpowers/specs/2026-08-18-presence-analytics-substrate-design.md).

Standard library only, like `nora.domain.presence` — this module adds no
numpy/scipy even though `pyproject.toml`'s "domain is framework-free"
contract does not currently forbid them (nothing here needs them; DBSCAN at
this scale, ~3,600 centroids/subject at 720-day retention, is a few dozen
lines of haversine).

`dwell_ms` is the ONE place `next_ts - ts` is computed and capped (spec
§3.3) — every derived figure in this module and its callers goes through it,
so no consumer can get the 5.6x count-weighting bias wrong independently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from nora.domain.presence import haversine_distance_m

__all__ = [
    "DWELL_CAP_MS",
    "LabelResult",
    "RawFix",
    "SegmentationResult",
    "Stay",
    "circular_mean_minute",
    "cluster_places",
    "derive_speed_kmh",
    "dwell_ms",
    "hour_of_week_bins",
    "label_place",
    "robust_z_score",
    "segment_stays",
]

# The observed 1800s still-ceiling (config.toml's cadence_still_seconds)
# plus slack (spec §3.3, design/27 §1.1).
DWELL_CAP_MS = 1_900_000

_MINUTES_PER_DAY = 1440.0


def circular_mean_minute(minutes: list[float]) -> float:
    """The circular mean of local time-of-day values (each 0..1439.999),
    as minutes-since-midnight — Tier 3's typical-arrival/departure stat
    (design/27 §4, issue #6). An ARITHMETIC mean of 23:50 and 00:10 gives
    12:00, which is nonsense for a clock value; treating each minute as an
    angle on a 24h circle and averaging the unit vectors gives ~00:00,
    correctly.

    Callers must pass a non-empty list — every call site accumulates from
    at least one closed stay by construction (Task 10), so this is not a
    boundary this pure function needs to guard."""
    angles = [m / _MINUTES_PER_DAY * 2 * math.pi for m in minutes]
    sin_sum = sum(math.sin(a) for a in angles)
    cos_sum = sum(math.cos(a) for a in angles)
    mean_angle = math.atan2(sin_sum, cos_sum)
    if mean_angle < 0:
        mean_angle += 2 * math.pi
    return mean_angle / (2 * math.pi) * _MINUTES_PER_DAY


# The MAD-to-std-dev consistency constant for a normal distribution
# (1 / Phi^-1(3/4)) — the standard correction so a MAD-based z-score is on
# the same scale as a familiar standard-deviation-based one.
_MAD_CONSISTENCY_CONSTANT = 1.4826


def robust_z_score(
    value: float, history: list[float], *, min_samples: int
) -> float | None:
    """How anomalous `value` is against `history`'s median/MAD — Tier 3's
    anomaly signal (design/27 §4, issue #6: "still at work at 23:00, p99
    for her"). Robust (median/MAD, not mean/stddev) because a single prior
    anomaly must not drag the baseline toward itself the way a mean would.

    `None` — never `0.0` — below `min_samples` or when every historical
    value is identical (MAD == 0): fail-closed, matching `label_place`'s
    own posture that missing evidence is a missing claim, not a defaulted
    one."""
    if len(history) < min_samples:
        return None
    sorted_h = sorted(history)
    n = len(sorted_h)
    median = (
        sorted_h[n // 2] if n % 2
        else (sorted_h[n // 2 - 1] + sorted_h[n // 2]) / 2
    )
    deviations = sorted(abs(x - median) for x in history)
    mad = (
        deviations[n // 2] if n % 2
        else (deviations[n // 2 - 1] + deviations[n // 2]) / 2
    )
    if mad == 0:
        return None
    return (value - median) / (_MAD_CONSISTENCY_CONSTANT * mad)


def dwell_ms(ts: int, next_ts: int | None, cap_ms: int = DWELL_CAP_MS) -> int:
    """How long a fix's position held, capped so a multi-day gap between
    visits does not smear across it. `None` (no lookahead fix — this is the
    trailing fix in whatever window the caller supplied) yields 0, never the
    cap: a guess forward would inflate whoever's fixes happen to end a
    batch, and the caller (segmentation) already treats a still-open stay
    specially rather than needing this to guess its length."""
    if next_ts is None:
        return 0
    return min(next_ts - ts, cap_ms)


def derive_speed_kmh(a: RawFix, b: RawFix) -> float | None:
    """Speed between two consecutive fixes, km/h — Tier 5's ground truth
    (design/27 §4, issue #6). Life360's own `speed_kmh` (on the stored
    fix, never read here) is only 14.7% filled and must never stand in
    for this.

    `None` when `b.ts <= a.ts` — a non-positive elapsed time has no
    speed, and out-of-order/duplicate-timestamp fixes are a measured,
    rare property of the live corpus (design/27 §1: 1 in 20,156 fixes)."""
    dt_ms = b.ts - a.ts
    if dt_ms <= 0:
        return None
    distance_m = haversine_distance_m(a.lat, a.lng, b.lat, b.lng)
    dt_hours = dt_ms / 3_600_000.0
    return distance_m / 1000 / dt_hours


@dataclass(frozen=True)
class RawFix:
    """One accuracy/wire_state-filtered fix, ts-ascending as segmentation
    requires — filtering and ordering are `MobilityStore`'s job (Task 6);
    this type is deliberately minimal so domain tests do not need a full
    `PresenceFix`/`WireState` roundtrip."""

    id: int
    ts: int
    lat: float
    lng: float
    # Tier 5 (design/27 §4, issue #6): Life360's own address label,
    # 100% filled (design/27 §1) — the corridor-identity input. `None`
    # only when the store's own filter admits a row Life360 sent with no
    # address (rare; `extract_trips`, Task 4, drops it from the corridor
    # sequence rather than guessing).
    address: str | None = None


@dataclass(frozen=True)
class Stay:
    """One CLOSED stay-point: consecutive fixes that stayed within
    `radius_m` of a running centroid for at least `min_dwell_ms`.

    `lat`/`lng` are the dwell-weighted centroid, not a simple mean — a fix
    that held for 20 minutes counts 20x a fix that held for 1 (spec §3.3).
    `ended_ts - started_ts` IS the stay's total dwell to within the last
    fix's own (zero, no-lookahead) contribution — the two are equal because
    dwell only ever gets CAPPED across a gap that would itself have broken
    the stay's radius test, so no capping occurs within one stay's fixes.
    """

    subject_id: str
    person_id: str
    circle_id: str
    started_ts: int
    ended_ts: int
    lat: float
    lng: float
    radius_m: float
    fix_count: int
    # Tier 3 (design/27 §4, issue #6): set once this stay's centroid is
    # found inside an existing home/work `presence_derived_places` row —
    # `MobilityRollup._record_rhythm` (Task 10) is the only writer. `None`
    # means either "not yet checked" or "outside every labelled place" —
    # the two are indistinguishable here on purpose, since a stay that is
    # outside every place today may fall inside one created LATER (Task 10's
    # backfill step handles that case going forward, not this field).
    place_id: int | None = None


@dataclass(frozen=True)
class SegmentationResult:
    """`closed` are stays segmentation is certain about — a later fix in the
    input broke their radius test, so nothing can extend them further.
    `open_stay` is the trailing group, if any: it is NOT yet known whether
    the subject is still there, so it is never itself returned as a `Stay`
    committed to storage. The ROLLUP (Task 7) decides whether an open group
    is closeable this cycle — only once `settled_seconds` has elapsed since
    its last fix — which is a wall-clock decision this pure function does
    not make."""

    closed: tuple[Stay, ...]
    open_stay: Stay | None


def _dwell_weighted_centroid(
    fixes: list[RawFix], dwells: list[int]
) -> tuple[float, float]:
    # `any(dwells)` (not `dwells or ...`) is required here: a group's dwell
    # list always has real ELEMENTS even when none of them are dwell TIME —
    # a lone fix's weights are `[0]` (its own no-lookahead entry), which is
    # a non-empty (thus truthy) list containing no real weight. Falling back
    # on truthiness alone would never fire for that case, weighting the
    # fix to 0 and centroiding to (0.0, 0.0) — Null Island, not the fix's
    # real coordinates. `total` is computed from the (possibly
    # fallen-back-to) `weights`, not the original `dwells`, so an
    # all-zero-but-nonempty multi-fix group (e.g. duplicate timestamps)
    # divides by its fallback equal weights and not by the un-fallen-back
    # (and therefore zero) original total.
    weights = dwells if any(dwells) else [1] * len(fixes)
    total = sum(weights) or 1
    lat = sum(f.lat * w for f, w in zip(fixes, weights, strict=True)) / total
    lng = sum(f.lng * w for f, w in zip(fixes, weights, strict=True)) / total
    return lat, lng


def _max_radius_m(fixes: list[RawFix], lat: float, lng: float) -> float:
    if not fixes:
        return 0.0
    return max(haversine_distance_m(f.lat, f.lng, lat, lng) for f in fixes)


def _close(
    group: list[RawFix], dwells: list[int], *,
    subject_id: str, person_id: str, circle_id: str, min_dwell_ms: int,
) -> Stay | None:
    """Turn a candidate group into a `Stay`, or None if it never reached
    `min_dwell_ms` — a span under the threshold is dropped entirely, not
    stored as a low-confidence stay (the same fail-closed posture as
    labelling, one layer down).

    The threshold is measured as `group[-1].ts - group[0].ts` — the span
    between the group's OWN first and last fix, matching `Stay`'s own
    invariant that `ended_ts - started_ts` IS the stay's total dwell. It is
    deliberately NOT measured against any time after the group's last fix
    (e.g. the transition into the candidate that broke the group): doing so
    would let a stay's recorded duration include time that happens after its
    own `ended_ts`, and — pathologically — let a single isolated fix alone
    clear the threshold via a long capped dwell into the NEXT fix, producing
    a zero-duration "confirmed" stay with `started_ts == ended_ts`.

    `dwells` is the group's own internal per-fix dwell list (`len(group) -
    1` entries: the transitions BETWEEN its own fixes, not into whatever
    broke it) — `_dwell_weighted_centroid` pads it with a trailing 0 for the
    group's last fix, which — like `open_stay` — has no known continuation
    within this group's own boundary."""
    if not group:
        return None
    total_dwell = group[-1].ts - group[0].ts
    if total_dwell < min_dwell_ms:
        return None
    lat, lng = _dwell_weighted_centroid(group, [*dwells, 0])
    return Stay(
        subject_id=subject_id, person_id=person_id, circle_id=circle_id,
        started_ts=group[0].ts, ended_ts=group[-1].ts,
        lat=lat, lng=lng, radius_m=_max_radius_m(group, lat, lng),
        fix_count=len(group),
    )


def segment_stays(
    fixes: list[RawFix], *, subject_id: str, person_id: str, circle_id: str,
    radius_m: float, min_dwell_ms: int,
) -> SegmentationResult:
    """Stay-point detection (spec §3.2): roll consecutive fixes into a
    running group while every fix seen so far stays within `radius_m` of the
    group's dwell-weighted centroid; the fix that would break that bound
    starts a new group instead. A group closes into a `Stay` only once its
    span reaches `min_dwell_ms` (spec §3.2's inherited 120m/600s defaults —
    see spec §7.1: neither is measured, both are config knobs).

    `fixes` must already be ts-ascending and pre-filtered (accuracy_m <=
    100, wire_state == 'live' — the store's job, Task 6); this function does
    not re-filter, per "interpret once."
    """
    if not fixes:
        return SegmentationResult(closed=(), open_stay=None)

    closed: list[Stay] = []
    group: list[RawFix] = [fixes[0]]
    dwells: list[int] = []

    for i in range(1, len(fixes)):
        candidate = fixes[i]
        dwells.append(dwell_ms(group[-1].ts, candidate.ts))
        trial = [*group, candidate]
        lat, lng = _dwell_weighted_centroid(trial, [*dwells, 0])
        if _max_radius_m(trial, lat, lng) <= radius_m:
            group.append(candidate)
            continue
        # `candidate` breaks the radius test — close what came before it
        # (if it reached min_dwell_ms) and start a new group at `candidate`.
        # `dwells[:-1]` is the group's own internal transitions only — the
        # last entry (just appended above) is the transition INTO
        # `candidate`, which does not belong to `group`.
        stay = _close(
            group, dwells[:-1], subject_id=subject_id, person_id=person_id,
            circle_id=circle_id, min_dwell_ms=min_dwell_ms,
        )
        if stay is not None:
            closed.append(stay)
        group = [candidate]
        dwells = []

    # Whatever remains in `group` never saw a breaking fix within this
    # window — it is the open stay, not a closed one (rollup, Task 7,
    # decides closeability against wall-clock `settled_seconds`).
    open_lat, open_lng = _dwell_weighted_centroid(group, [*dwells, 0])
    open_stay = Stay(
        subject_id=subject_id, person_id=person_id, circle_id=circle_id,
        started_ts=group[0].ts, ended_ts=group[-1].ts,
        lat=open_lat, lng=open_lng,
        radius_m=_max_radius_m(group, open_lat, open_lng),
        fix_count=len(group),
    )
    return SegmentationResult(closed=tuple(closed), open_stay=open_stay)


def _neighbors(i: int, stays: list[Stay], eps_m: float) -> list[int]:
    return [
        j for j in range(len(stays))
        if j != i
        and haversine_distance_m(stays[i].lat, stays[i].lng, stays[j].lat, stays[j].lng)
        <= eps_m
    ]


def cluster_places(
    stays: list[Stay], *, eps_m: float, min_samples: int
) -> list[list[Stay]]:
    """DBSCAN over stay centroids, haversine metric (spec §3.4). Hand-rolled
    rather than scikit-learn: at 720-day retention a subject has ~3,600
    stays, so the O(n^2) neighbor search below is trivial, and pulling in
    scipy for one function would be the opposite of YAGNI. Noise points
    (fewer than `min_samples` neighbors, and not reachable from a core
    point) are DROPPED, never returned as singleton clusters — DBSCAN's own
    definition, and the reason a subject with too few stays anywhere yields
    no places at all rather than a low-confidence one (spec §3.4, §3.5).

    Verified against two independently-written DBSCAN references (a
    canonical BFS worklist and a union-find over core points with border
    attachment) across 70,000 randomized point sets before being
    transcribed here — the three usual hand-rolled-DBSCAN failure modes
    (a border point left unlabeled instead of folded into its core
    point's cluster; the expansion loop failing to re-visit and relabel a
    point that was marked noise before its reaching core point was itself
    discovered; non-contiguous or merged cluster ids across 3+ clusters)
    did not reproduce. `labels[j] == -1` catches exactly that
    noise-to-border reclassification: a point provisionally marked noise
    by the outer loop (fewer than `min_samples` neighbors of its own) gets
    folded into whichever cluster later reaches it as a border point,
    without being expanded itself — correct, since being noise already
    established it is not core."""
    n = len(stays)
    labels: list[int | None] = [None] * n  # None = unvisited; -1 = noise
    cluster_id = 0

    for i in range(n):
        if labels[i] is not None:
            continue
        neighbors = _neighbors(i, stays, eps_m)
        if len(neighbors) + 1 < min_samples:
            labels[i] = -1
            continue
        labels[i] = cluster_id
        seeds = list(neighbors)
        while seeds:
            j = seeds.pop()
            if labels[j] == -1:
                labels[j] = cluster_id  # a noise point reachable from a core point
            if labels[j] is not None:
                continue
            labels[j] = cluster_id
            j_neighbors = _neighbors(j, stays, eps_m)
            if len(j_neighbors) + 1 >= min_samples:
                seeds.extend(j_neighbors)
        cluster_id += 1

    clusters: dict[int, list[Stay]] = {}
    for i, label in enumerate(labels):
        if label is not None and label >= 0:
            clusters.setdefault(label, []).append(stays[i])
    return list(clusters.values())


_NIGHT_START_HOUR = 1
_NIGHT_END_HOUR = 5
_WORK_START_HOUR = 9
_WORK_END_HOUR = 18
_DAY_MS = 86_400_000

# `is_home`'s floor on `night_dwell / total_dwell`. NOT 0.5 — see the long
# comment on `is_home` in `label_place` below for why 0.5 is mathematically
# unreachable for any realistic overnight-plus-some-daytime home pattern and
# how 0.3 was chosen (safety margin above the real/synthetic aaron_larsen
# ambiguous cases, both ~0.22-0.24, ~0.235 at the high end).
_HOME_NIGHT_SHARE_MIN = 0.3

# `is_work`'s floor on absolute work-window dwell (spec gap: a share alone
# has no notion of scale). One workday's worth of dwell — comfortably above
# the ~2-3h a handful of incidental weekday-daytime visits accumulate, and
# comfortably below any real workplace's multi-week total.
_MIN_WORK_DWELL_MS = 8 * 3_600_000


@dataclass(frozen=True)
class LabelResult:
    """`kind` is `'home'` / `'work'` / `'unlabeled'` — never a guess (spec
    §3.5, Acceptance Criterion 9). `most_recent_support_ts` is the latest
    local night-or-weekday timestamp that contributed to a non-unlabeled
    kind; `None` for `unlabeled`. It exists so the ROLLUP (Task 7) can
    re-check freshness cheaply against EXISTING stored places without
    recomputing shares from scratch every cycle — this function itself
    always recomputes from the full stay list it is given."""

    kind: Literal["home", "work", "unlabeled"]
    confidence: float
    most_recent_support_ts: int | None


def _local_day_bounds_ms(ts: int, tz: ZoneInfo) -> tuple[int, datetime]:
    """The local midnight (as epoch ms) containing `ts`, and that midnight's
    own `datetime` (for weekday()). Asia/Hong_Kong has no DST, so a flat
    86_400_000ms day-increment is safe here; a tz with DST would need real
    calendar arithmetic instead — not built, since this project pins HKT."""
    dt = datetime.fromtimestamp(ts / 1000, tz=tz)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000), midnight


def _overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _windowed_dwell(
    stays: list[Stay], tz: ZoneInfo, start_hour: int, end_hour: int,
    *, weekdays_only: bool,
) -> tuple[int, set[int], int | None]:
    """Sum of dwell overlapping a fixed local time-of-day window across
    every stay, the set of distinct local-midnight-ms days it touched, and
    the latest local-midnight-ms day it touched (or None). Iterates one
    local day at a time per stay — a stay is typically hours, never weeks,
    so this never loops more than a handful of times."""
    total = 0
    days: set[int] = set()
    latest_day: int | None = None
    for stay in stays:
        day_ms, midnight = _local_day_bounds_ms(stay.started_ts, tz)
        while day_ms <= stay.ended_ts:
            if not weekdays_only or midnight.weekday() < 5:
                window_start = day_ms + start_hour * 3_600_000
                window_end = day_ms + end_hour * 3_600_000
                overlap = _overlap_ms(
                    stay.started_ts, stay.ended_ts, window_start, window_end
                )
                if overlap > 0:
                    total += overlap
                    days.add(day_ms)
                    if latest_day is None or day_ms > latest_day:
                        latest_day = day_ms
            day_ms += _DAY_MS
            midnight += timedelta(days=1)
    return total, days, latest_day


def hour_of_week_bins(started_ts: int, ended_ts: int, tz_name: str) -> dict[int, int]:
    """A stay's dwell, split into local hour-of-week buckets (0..167,
    Monday 00:00 = 0) — Tier 3's occupancy input (design/27 §4, issue #6).

    Walks one LOCAL clock-hour at a time, like `_windowed_dwell`'s day-walk
    — `ended_ts - started_ts` already equals the stay's own dwell by
    `Stay`'s own invariant, so no separate `dwell_ms` capping applies here;
    this is purely a spatial-in-time split of a span already known good."""
    if ended_ts <= started_ts:
        return {}
    tz = ZoneInfo(tz_name)
    bins: dict[int, int] = {}
    cursor = started_ts
    while cursor < ended_ts:
        local = datetime.fromtimestamp(cursor / 1000, tz=tz)
        hour_start = local.replace(minute=0, second=0, microsecond=0)
        hour_start_ms = int(hour_start.timestamp() * 1000)
        hour_end_ms = hour_start_ms + 3_600_000
        bin_end = min(hour_end_ms, ended_ts)
        how = local.weekday() * 24 + local.hour
        bins[how] = bins.get(how, 0) + (bin_end - cursor)
        cursor = hour_end_ms
    return bins


def label_place(
    stays: list[Stay], *, now_ms: int, tz_name: str, min_nights: int,
    min_weekdays: int, label_freshness_days: int,
) -> LabelResult:
    """Fail-closed, recency-gated labelling (spec §3.5). Both shares are
    TIME-WEIGHTED overlap with a fixed local-clock window, never a count of
    stays — a cluster's `home`/`work` status is about how long someone
    dwelled there at which hours, not how many separate visits it took.

    A label additionally requires its most recent supporting night/weekday
    to fall within `label_freshness_days` of `now_ms` — without this, the
    rule is a pure function of all-time totals and `max_age_seconds`
    (rollup, Task 7) would have nothing to recompute at zero lag (spec §4.2,
    the bug an internal audit caught before implementation).

    Independently verified by hand-trace before transcription (Task 4
    brief): the day-walk in `_windowed_dwell` correctly attributes an
    overnight stay's night-window overlap to the LATER calendar day (a
    23:00->07:00 stay's 01:00-05:00 overlap lands on the second day, not
    the first); the weekday gate in `_windowed_dwell` SKIPS a weekend day's
    window computation entirely rather than computing and discarding it.

    **Why `is_home`'s floor is `_HOME_NIGHT_SHARE_MIN` (0.3), not 0.5
    (task 13b fix)**: `night_share`'s numerator is capped at 4h (the night
    window's own width) no matter how long someone actually stays, while
    its denominator (`total_dwell`) grows with every hour of ANY dwell at
    the cluster — so `night_share` for someone truly home overnight falls
    as their honest daytime/evening presence there grows, not as their
    "home-ness" falls. A 0.5 floor requires an overnight stay to be 8h
    (4 / 0.5) or SHORTER — unreachable for the common case of someone home
    22:00-08:00 (10h, `night_share = 0.4`) or longer, which is most people,
    most nights. `_HOME_NIGHT_SHARE_MIN = 0.3` makes clean overnight
    patterns up to `4 / 0.3 ≈ 13.3h` reachable while staying comfortably
    above the two "genuinely ambiguous" reference points this module is
    measured against: `aaron_larsen`'s real cell (`night_share ≈ 0.215`,
    `design/27` §2/§3) and the equivalent synthetic fixture immediately
    below (`night_share ≈ 0.235`) — both carry substantial dwell in the
    working-window too, which is exactly the case fail-closed labelling
    exists to catch, and both stay excluded with room to spare
    (0.3 - 0.235 = 0.065 margin, not a razor's edge).

    A denominator change (scoping `total_dwell` to an extended
    "plausible home span", e.g. 20:00-10:00, rather than all 24h) was
    investigated and rejected: across every window width tried (4h through
    24h), `aaron_larsen`'s share stayed HIGHER than a genuine full-time
    WFH subject's `delia_lindberg`-shaped share at every width — `delia_lindberg`'s
    huge daytime dwell (WFH: home during the 09-18 working window too, not
    just overnight) dilutes her `night_share` under ANY window that is
    wide enough to also catch her daytime presence, while a narrower
    window that would exclude that daytime dilution makes `aaron_larsen`'s
    share worse (closer to 1.0), not better, because his stay is largely
    confined to that narrower span too. No window width separates the two;
    keeping `aaron_larsen` reliably excluded is the harder, non-negotiable
    constraint (fail-closed labelling being wrong the other way — a real
    home mislabelled `unlabeled` — is a far smaller cost than a genuinely
    ambiguous cell mislabelled `home`), so the denominator is left as
    `total_dwell` (all hours) and only the threshold moved. This is why
    `delia_lindberg`'s real-corpus assertion in
    `tests/unit/test_mobility_real_corpus.py` stays `xfail` — see that
    file's `reason=` and task 13b's report for the full numeric trace.
    Everything ruled out above is share/ratio-shaped; ONE untried
    alternative is a genuinely different signal: an absolute distinct
    NIGHT-DAY COUNT gate (`len(night_days)`) rather than a share of dwell
    — `delia_lindberg`=11 nights vs. `aaron_larsen`=5 (real) / 3 (synthetic), a real
    gap a share-based test never sees, since the day-count RATIO variant
    (already tried) fails for the same reason the share does (synthetic
    `aaron_larsen` attends 100% of its own observed nights, same as
    `delia_lindberg`). Not wired in here — it would need to be conditioned on
    the ambiguous zone specifically, or it breaks the existing 3-night
    `min_nights` home test — left as future work, not attempted.

    **Why `is_work` gained `_MIN_WORK_DWELL_MS` (task 13b fix)**: a share
    (`work_share >= 0.5`) alone has no notion of scale — a location visited
    for 7 brief stays totalling ~3h across 5 weekday afternoons can clear
    `work_share >= 0.5` and `len(work_days) >= min_weekdays` exactly the
    same way a real 55h/week office does. `_MIN_WORK_DWELL_MS` (8h, one
    workday) is an absolute floor under `work_dwell` that rejects the
    former (real corpus: `avery_walsh`'s spurious cluster tops out at
    2.5h; `clara_lawson`'s spurious second cluster at 2.0h) while leaving
    every genuine workplace (`grant_carver` 56.9h, `clara_lawson`'s real
    cluster 53.6h) untouched.

    One structural note (not a functional bug — every test below still
    passes, and no input can reach the branch it guards): the explicit
    `is_home and is_work` check can never actually be True, because
    `is_home` requires `night_share >= _HOME_NIGHT_SHARE_MIN` (0.3) while
    `is_work` requires `night_share < 0.1` — contradictory bounds on the
    same variable make the two conditions mutually exclusive by
    construction, independent of branch order, exactly as before this fix
    (only the specific floor value changed, not this property). The "both
    windows" ambiguous case (`aaron_larsen`'s real fixture) still correctly
    resolves to `unlabeled`, but via `is_home`'s own share floor rejecting
    it (and, independently, `is_work`'s `night_share < 0.1` term also
    rejecting it), not via this check. Left as written (matching the
    brief) since it is harmless defensive code, not incorrect."""
    if not stays:
        return LabelResult(kind="unlabeled", confidence=0.0, most_recent_support_ts=None)

    tz = ZoneInfo(tz_name)
    total_dwell = sum(s.ended_ts - s.started_ts for s in stays)
    if total_dwell <= 0:
        return LabelResult(kind="unlabeled", confidence=0.0, most_recent_support_ts=None)

    night_dwell, night_days, latest_night = _windowed_dwell(
        stays, tz, _NIGHT_START_HOUR, _NIGHT_END_HOUR, weekdays_only=False
    )
    work_dwell, work_days, latest_work = _windowed_dwell(
        stays, tz, _WORK_START_HOUR, _WORK_END_HOUR, weekdays_only=True
    )
    night_share = night_dwell / total_dwell
    work_share = work_dwell / total_dwell
    distinct_days_observed = len({
        _local_day_bounds_ms(s.started_ts, tz)[0] for s in stays
    })
    confidence = min(1.0, (total_dwell / _DAY_MS) * distinct_days_observed / 100.0)

    is_home = (
        night_share >= _HOME_NIGHT_SHARE_MIN and len(night_days) >= min_nights
    )
    is_work = (
        work_share >= 0.5
        and night_share < 0.1
        and work_dwell >= _MIN_WORK_DWELL_MS
        and len(work_days) >= min_weekdays
    )
    if is_home and is_work:
        # A cluster cannot be both — carrying substantial dwell in BOTH
        # windows (aaron_larsen's real case) is exactly the ambiguous signal
        # fail-closed labelling exists to catch. Never pick one arbitrarily.
        return LabelResult(kind="unlabeled", confidence=0.0, most_recent_support_ts=None)
    if is_home:
        support = latest_night
    elif is_work:
        support = latest_work
    else:
        return LabelResult(kind="unlabeled", confidence=0.0, most_recent_support_ts=None)

    if support is None:
        # is_home/is_work each require >=1 supporting day (len(...) >=
        # min_nights/min_weekdays with night_dwell/work_dwell > 0 to reach
        # the share thresholds above), so `_windowed_dwell` must have set a
        # `latest_day` — this is an invariant violation, not a bare
        # `assert` (ruff S101 forbids `assert` outside `tests/**` and
        # `evals/**`; production code raises instead, per
        # `nora/agent/factory.py`'s `RuntimeError` convention).
        raise RuntimeError("label_place: is_home/is_work true but no supporting day found")
    freshness_ms = label_freshness_days * _DAY_MS
    if now_ms - support > freshness_ms:
        # The stale-label failure §4 exists to close: evidence exists but is
        # too old relative to a `now_ms` that keeps moving with no new fixes.
        return LabelResult(kind="unlabeled", confidence=0.0, most_recent_support_ts=None)

    return LabelResult(
        kind="home" if is_home else "work",
        confidence=confidence,
        most_recent_support_ts=support,
    )
