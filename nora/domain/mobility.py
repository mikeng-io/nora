"""Pure mobility derivation — stay segmentation, place clustering, labelling.

Standard library only, like `nora.domain.presence` — this module adds no
numpy/scipy even though `pyproject.toml`'s "domain is framework-free"
contract does not currently forbid them (nothing here needs them; DBSCAN at
this scale is a few dozen lines of haversine).

`dwell_ms` is the ONE place `next_ts - ts` is computed and capped — every
derived figure in this module and its callers goes through it, so no consumer
can get the count-weighting bias wrong independently.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

from nora.domain.presence import haversine_distance_m

__all__ = [
    "CHUNK_GAP_MS",
    "DWELL_CAP_MS",
    "TAG_MERGE_RADIUS_M",
    "DerivedPlace",
    "LabelResult",
    "PlannedPlace",
    "RawFix",
    "SegmentationResult",
    "Stay",
    "TaggedPlace",
    "Trip",
    "best_corridor_match",
    "circular_mean_minute",
    "cluster_places",
    "cluster_trips",
    "collapse_duplicate_fixes",
    "derive_speed_kmh",
    "dwell_ms",
    "dwell_weighted_centroid",
    "edit_distance",
    "extract_trips",
    "hour_of_week",
    "hour_of_week_bins",
    "label_place",
    "mode_address",
    "plan_places",
    "robust_z_score",
    "segment_stays",
    "trailing_fix_is_settled",
]

# The observed 1800 s still-ceiling plus slack.
#
# The ceiling is LIFE360's own resting report cadence, NOT Nora's poll interval
# (`config.toml`'s `cadence_still_seconds` is 60 in the deployment and 300 by
# default). This constant is also the OPEN-STAY CLOSE GATE
# (`MobilityRollup.run_once`): tracking `cadence_still_seconds` would set it
# near 60 s and fragment stays (many short stays instead of one clean one).
#
# The number is bounded by measurement, not by config: inter-fix gaps between
# two fixes of ONE stay run p90 1,805 s / p99 1,828 s, so 1,900 s sits ~1.2 min
# above the p99 and closes a stay on the clock for under 1% of within-stay
# gaps. Re-measure that distribution before moving it.
DWELL_CAP_MS = 1_900_000

# The batch-split threshold for `_chunk_by_gap` — how long a gap between two
# consecutive fixes must be before they are treated as SEPARATE visits rather
# than one continuous presence. Deliberately a CROSS-DAY span, NOT the
# `DWELL_CAP_MS` dwell ceiling: Life360's resting cadence is one fix every
# 10-55 minutes, so a subject who stays home all day produces fixes 30+
# minutes apart that a 31.7-min split would carve into dozens of sub-min-dwell
# fragments instead of one long stay. A 12h gap separates genuine day-to-day
# returns while keeping an all-day home presence in ONE chunk. `dwell_ms`'s own
# cap stays `DWELL_CAP_MS` — that is a still-ceiling for how long a single
# fix's position may be credited, a different job from batch partitioning.
CHUNK_GAP_MS = 12 * 3_600_000

_MINUTES_PER_DAY = 1440.0


def circular_mean_minute(minutes: list[float]) -> float:
    """The circular mean of local time-of-day values (each 0..1439.999),
    as minutes-since-midnight — Tier 3's typical-arrival/departure stat
    An ARITHMETIC mean of 23:50 and 00:10 gives
    12:00, which is nonsense for a clock value; treating each minute as an
    angle on a 24h circle and averaging the unit vectors gives ~00:00,
    correctly.

    Callers must pass a non-empty list — every call site accumulates from
    at least one closed stay by construction, so this is not a
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
    value: float, history: list[float], *, min_samples: int,
    min_mad: float = 0.0,
) -> float | None:
    """How anomalous `value` is against `history`'s median/MAD — Tier 3's
    anomaly signal ("still at work at 23:00, p99 for her"). Robust (median/MAD, not mean/stddev)
    because a single prior anomaly must not drag the baseline toward itself the way a mean would.

    `None` — never `0.0` — below `min_samples` or when every historical
    value is identical (MAD == 0): fail-closed, matching `label_place`'s
    own posture that missing evidence is a missing claim, not a defaulted
    one.

    `min_mad` floors the MAD: a baseline whose values
    happen to agree to within seconds is not evidence that a deviation of
    seconds is abnormal. Without a floor, `presence_place_rhythm` MADs of
    500-5,000 ms against ~30-minute medians tripped |z| >= 3 on a two-second
    deviation, and the counter reported the sampler rather than behaviour.
    Pass the smallest deviation that could MEAN something at the quantity's
    own resolution."""
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
    mad = max(mad, min_mad)
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
    """Speed between two consecutive fixes, km/h — Tier 5's ground truth.
    Life360's own `speed_kmh` (on the stored
    fix, never read here) is only 14.7% filled and must never stand in
    for this.

    `None` when `b.ts <= a.ts` — a non-positive elapsed time has no
    speed, and out-of-order/duplicate-timestamp fixes are a rare
    property of the live corpus."""
    dt_ms = b.ts - a.ts
    if dt_ms <= 0:
        return None
    distance_m = haversine_distance_m(a.lat, a.lng, b.lat, b.lng)
    dt_hours = dt_ms / 3_600_000.0
    return distance_m / 1000 / dt_hours


def edit_distance(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """Classic Levenshtein distance over two corridor address sequences —
    Tier 5's trip-similarity metric (trips cluster by
    address-sequence edit distance). Hand-rolled, O(len(a) *
    len(b)) — corridor sequences are short (a handful of road names per
    trip), matching `cluster_places`'s own "trivial at this scale, no
    scipy" precedent."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, y in enumerate(b, start=1):
            cost = 0 if x == y else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost, # substitution / match
            )
        prev = curr
    return prev[-1]


def best_corridor_match(
    candidates: Iterable[tuple[int, tuple[str, ...]]], *,
    sequence: tuple[str, ...], max_edit_distance: int,
) -> tuple[int, tuple[str, ...]] | None:
    """The nearest of `candidates` (`(corridor_id, sequence)` pairs) within
    `max_edit_distance` of `sequence`, or `None`. **Ties break on the
    LOWEST corridor id.**

    This lives in the domain, not in either store, because BOTH
    `MobilityStore` implementations have to answer `existing_corridor`
    identically: a per-store copy of this loop would resolve a tie on
    whatever order Postgres returns its heap in (`SqlMobilityStore`) or on dict
    insertion order (`FakeMobilityStore`). Two
    corridors equidistant from a new trip's sequence is routine, not
    exotic, at `corridor_max_edit_distance=2` over short sequences, and
    resolving it differently on different days splits one real route's
    samples across two corridor rows.

    The `(dist, cid)` comparison makes the answer independent of the order
    `candidates` arrives in, so no caller has to sort first for
    correctness. `SqlMobilityStore` still adds `ORDER BY id ASC` — cheap on
    an already-indexed column, and it keeps the SQL readable as the same
    rule — but the rule does not depend on it."""
    best: tuple[int, int, tuple[str, ...]] | None = None  # (dist, id, seq)
    for cid, candidate in candidates:
        dist = edit_distance(sequence, candidate)
        if dist > max_edit_distance:
            continue
        if best is None or (dist, cid) < (best[0], best[1]):
            best = (dist, cid, candidate)
    return None if best is None else (best[1], best[2])


@dataclass(frozen=True)
class RawFix:
    """One accuracy/wire_state-filtered fix, ts-ascending as segmentation
    requires — filtering and ordering are `MobilityStore`'s job;
    this type is deliberately minimal so domain tests do not need a full
    `PresenceFix`/`WireState` roundtrip."""

    id: int
    ts: int
    lat: float
    lng: float
    # Tier 5: Life360's own address label — the corridor-identity input.
    # `None` only when the store's own filter admits a row Life360 sent with no
    # address (rare; `extract_trips` drops it from the corridor sequence
    # rather than guessing).
    address: str | None = None
    # Trip settlement: Life360's own per-fix device signal — the PRIMARY
    # input for deciding whether a trailing movement group is still
    # ongoing. `None` only when the store's own filter admits a row
    # with no reading; `extract_trips` falls back to a time-based check
    # in that case, never guesses `True`/`False` — and for that same
    # `None` case, its caller (`nora.analytics.rollup.run_once`) may
    # separately apply a dwell-based judgment (DEC-125's gate 2,
    # `tail_dwelled`), resolved before `extract_trips` is ever called.
    is_driving: bool | None = None


@dataclass(frozen=True)
class Stay:
    """One CLOSED stay-point: consecutive fixes that stayed within
    `radius_m` of a running centroid for at least `min_dwell_ms`.

    `lat`/`lng` are the dwell-weighted centroid, not a simple mean — a fix
    that held for 20 minutes counts 20x a fix that held for 1 (`dwell_ms`).
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
    # Derived-place address label: the MODE of this
    # stay's fixes' `address` — Life360's own free place string — chosen once at segmentation time
    # via `mode_address` below (most frequent non-None, first occurrence on a
    # tie, `None` when every fix lacked an address). This is the in-memory
    # input for the rollup's place-label FALLBACK: the Google
    # geocode cache stays canonical whenever it has a hit, and this field
    # only fills the gap when it does not. Deliberately NOT persisted —
    # `presence_stays` has no address column, so a stay re-read from the
    # store carries `None` and `run_once` re-attaches this cycle's
    # addresses from its own segmentation output instead of trusting the
    # roundtrip.
    address: str | None = None
    # Tier 3: set once this stay's centroid is
    # found inside an existing home/work `presence_derived_places` row —
    # `MobilityRollup._reconcile_places` (DEC-190) is the only writer. `None`
    # means either "not yet checked" or "outside every labelled place" —
    # the two are indistinguishable here on purpose, since a stay that is
    # outside every place today may fall inside one created LATER (a backfill
    # step handles that case, not this field).
    place_id: int | None = None


@dataclass(frozen=True)
class Trip:
    """One movement span between stays — the
    complement of `Stay`: fixes NOT absorbed into any stay-forming group.
    `distance_m`/`avg_speed_kmh` are derived from the trip's OWN fixes
    (`derive_speed_kmh` pairwise, summed), never from Life360's
    `speed_kmh`. `corridor` is the deduplicated address sequence — the
    corridor-CLUSTERING input; `corridor_id` is set once
    clustered, `None` until then, the same nullable-then-filled
    shape `Stay.place_id` uses."""

    subject_id: str
    person_id: str
    circle_id: str
    started_ts: int
    ended_ts: int
    distance_m: float
    avg_speed_kmh: float
    fix_count: int
    corridor: tuple[str, ...]
    corridor_id: int | None = None


def _trip_from_group(
    group: list[RawFix], *, subject_id: str, person_id: str, circle_id: str,
) -> Trip:
    distance_m = 0.0
    speeds: list[float] = []
    for a, b in pairwise(group):
        distance_m += haversine_distance_m(a.lat, a.lng, b.lat, b.lng)
        speed = derive_speed_kmh(a, b)
        if speed is not None:
            speeds.append(speed)
    duration_hours = (group[-1].ts - group[0].ts) / 3_600_000.0
    avg_speed = (
        distance_m / 1000 / duration_hours if duration_hours > 0
        else (sum(speeds) / len(speeds) if speeds else 0.0)
    )
    corridor: list[str] = []
    for f in group:
        if f.address is None:
            continue
        if not corridor or corridor[-1] != f.address:
            corridor.append(f.address)
    return Trip(
        subject_id=subject_id, person_id=person_id, circle_id=circle_id,
        started_ts=group[0].ts, ended_ts=group[-1].ts,
        distance_m=distance_m, avg_speed_kmh=avg_speed,
        fix_count=len(group), corridor=tuple(corridor),
    )


def trailing_fix_is_settled(
    last: RawFix, *, now_ms: int, settled_seconds: float
) -> bool:
    """Does `last` — the newest fix of a trailing, still-undecided group —
    say that group has stopped moving?

    `is_driving is True` is Life360 saying movement is ongoing, so no
    amount of elapsed wall-clock settles it. `False` is Life360 saying it
    stopped, which settles immediately. `None` is **no reading at all** —
    a nullable column, and the real state of any fix Life360 omits the flag on — and the only
    honest fallback there is the same quiet-boundary clock Tier 1 already uses for a
    stay's arrival. A `None` fix must never be treated as settled just
    because it isn't `True`: that is the fragmentation bug with the signal
    simply absent.

    Covers **gate 1 only** — the `is_driving`/`settled_seconds` check — of
    the two gates `run_once` combines to decide whether a trailing group
    is genuinely undecided (DEC-125). Shared on purpose between the two
    callers that must agree on THIS check specifically: `extract_trips`
    below (finalize this group, or hold it back?) and
    `nora.analytics.rollup.run_once` (is this trailing group a
    stay-in-progress at all, or undecided movement?) — two copies of gate
    1 would drift, and the two callers disagreeing on it means the same
    fixes finalized as a trip AND written as a stay. Gate 2 — the dwell
    check for the `is_driving is None` case — is a separate, NOT-shared
    computation that lives only in `run_once` (its `tail_dwelled`); this
    function has no knowledge of it, and `extract_trips` never calls gate
    2 either — it only ever sees the already-RESOLVED
    `open_stay_started_ts` that `run_once` hands it after combining both
    gates itself. That resolved boundary, not a second shared function,
    is what keeps the two callers from disagreeing on the final outcome
    even though gate 2 is not shared code."""
    if last.is_driving is True:
        return False
    if last.is_driving is None:
        return (now_ms - last.ts) / 1000 >= settled_seconds
    return True


def extract_trips(
    fixes: list[RawFix], closed_stays: list[Stay], *, subject_id: str,
    person_id: str, circle_id: str, min_trip_fixes: int,
    open_stay_started_ts: int | None,
    now_ms: int | None = None,
    settled_seconds: float | None = None,
    max_trip_duration_seconds: float | None = None,
) -> tuple[list[Trip], list[RawFix]]:
    """The movement fixes NOT covered by any CLOSED stay, grouped into
    trips — Tier 5's segmentation counterpart to `segment_stays`.

    `fixes` and `closed_stays` come from the SAME batch `segment_stays`
    already processed this cycle (`nora.analytics.rollup`) —
    `closed_stays` marks the ranges to EXCLUDE, not re-derive.
    `open_stay_started_ts`, when not `None`, excludes every fix from that
    point onward too: a real stay boundary was found, so the trip
    preceding it is already definite and needs no further check. A short
    run below `min_trip_fixes` is dropped as noise (a stray fix between
    two stays), the same fail-closed posture `_close`'s own `min_dwell_ms`
    gate uses for stays.

    When `open_stay_started_ts` is `None` — no stay boundary was found
    at all this cycle, meaning the trailing group's fate is genuinely
    undetermined — the trailing group is held back rather than finalized
    unless `now_ms` says otherwise: settled when its last fix's `is_driving` is `False`, or
    (when `is_driving is None`) once `settled_seconds` has elapsed since
    that fix, or unconditionally once `max_trip_duration_seconds` is
    exceeded (the stuck-signal safety valve). Returns `(trips,
    held_back)` — `held_back` carries the undecided tail's own fixes so
    the caller can hold the watermark back rather than losing them.
    `now_ms=None` (the default, what a non-final chunk passes) skips the
    settlement check entirely and always flushes the trailing group."""
    if not fixes:
        return [], []
    in_stay_ranges = [(s.started_ts, s.ended_ts) for s in closed_stays]

    def _in_a_stay(ts: int) -> bool:
        return any(start <= ts <= end for start, end in in_stay_ranges)

    trips: list[Trip] = []
    buffer: list[RawFix] = []
    for f in fixes:
        if open_stay_started_ts is not None and f.ts >= open_stay_started_ts:
            break
        if _in_a_stay(f.ts):
            if len(buffer) >= min_trip_fixes:
                trips.append(_trip_from_group(
                    buffer, subject_id=subject_id, person_id=person_id,
                    circle_id=circle_id,
                ))
            buffer = []
            continue
        buffer.append(f)

    held_back: list[RawFix] = []
    if buffer:
        settled = True
        if open_stay_started_ts is None and now_ms is not None:
            span_s = (buffer[-1].ts - buffer[0].ts) / 1000
            forced = (
                max_trip_duration_seconds is not None
                and span_s >= max_trip_duration_seconds
            )
            if not forced:
                settled = trailing_fix_is_settled(
                    buffer[-1], now_ms=now_ms,
                    # Fail CLOSED, not open: `settled_seconds or 0.0` would
                    # make an unsupplied `settled_seconds` settle an
                    # `is_driving=None` tail INSTANTLY (elapsed since
                    # basically anything is `>= 0`), fragmenting trips for
                    # exactly the fixes this fallback exists to protect.
                    # `inf` means "never sufficient" instead. Callers pass
                    # both together, so this is belt-and-braces.
                    settled_seconds=(
                        settled_seconds if settled_seconds is not None
                        else float("inf")
                    ),
                )
        if settled:
            if len(buffer) >= min_trip_fixes:
                trips.append(_trip_from_group(
                    buffer, subject_id=subject_id, person_id=person_id,
                    circle_id=circle_id,
                ))
        else:
            held_back = buffer
    return trips, held_back


def _trip_neighbors(i: int, trips: list[Trip], max_edit_distance: int) -> list[int]:
    return [
        j for j in range(len(trips))
        if j != i and edit_distance(trips[i].corridor, trips[j].corridor) <= max_edit_distance
    ]


def cluster_trips(
    trips: list[Trip], *, max_edit_distance: int, min_samples: int
) -> list[list[Trip]]:
    """DBSCAN over trip corridor sequences, edit-distance metric.

    Same algorithm shape as `cluster_places`
    (noise points below `min_samples` are DROPPED, never a singleton
    cluster), deliberately NOT sharing code with it: a parallel
    implementation costs ~20 lines, and a generic metric-parameterized
    version would put `cluster_places`'s verification at risk.

    **Callers MUST hold out trips with an empty `corridor` before calling
    this.** An address-less trip (every fix in its group had no `address`)
    is NOT harmlessly self-segregating: `edit_distance((), x) == len(x)`,
    so an empty sequence is within a typical `max_edit_distance` of ANY
    short real corridor and this function will legitimately put it in the
    same cluster as three real, named trips.
    What happens next depends on input order and is wrong both ways round —
    judging the cluster by its first trip discards a whole real cluster when
    that trip is the empty one, and contaminates a real corridor's sample
    count and speed percentiles when it is not.

    The one caller, `MobilityRollup._derive_corridors`, enforces the hold-out
    (it clusters only trips with a non-empty `corridor`), so an address-less
    trip ends with `corridor_id` NULL, the honest record of "no address, so no
    route". Any other caller owes the same filter.

    The usual hand-rolled-DBSCAN failure modes `cluster_places`'s own
    docstring names (a border point left unlabeled instead of folded into its
    core point's cluster; the expansion loop failing to re-visit and relabel a
    point marked noise before its reaching core point was discovered;
    non-contiguous or merged cluster ids across 3+ clusters) apply here too."""
    n = len(trips)
    labels: list[int | None] = [None] * n  # None = unvisited; -1 = noise
    cluster_id = 0

    for i in range(n):
        if labels[i] is not None:
            continue
        neighbors = _trip_neighbors(i, trips, max_edit_distance)
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
            j_neighbors = _trip_neighbors(j, trips, max_edit_distance)
            if len(j_neighbors) + 1 >= min_samples:
                seeds.extend(j_neighbors)
        cluster_id += 1

    clusters: dict[int, list[Trip]] = {}
    for i, label in enumerate(labels):
        if label is not None and label >= 0:
            clusters.setdefault(label, []).append(trips[i])
    return list(clusters.values())


@dataclass(frozen=True)
class SegmentationResult:
    """`closed` are stays segmentation is certain about — a later fix in the
    input broke their radius test, so nothing can extend them further.
    `open_stay` is the trailing group, if any: it is NOT yet known whether
    the subject is still there, so it is never itself returned as a `Stay`
    committed to storage. The ROLLUP decides whether an open group
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
    #
    # A LEFT FOLD, written out rather than `sum()`: `sum()` of floats is
    # compensated since Python 3.12, so its last bits depend on every term at
    # once, while `segment_stays` keeps the same sums running as a group grows
    # (`_RunningGroup`) and must reach bit-for-bit the value this computes.
    weights = dwells if any(dwells) else [1] * len(fixes)
    total = sum(weights) or 1
    lat_sum = 0.0
    lng_sum = 0.0
    for f, w in zip(fixes, weights, strict=True):
        lat_sum += f.lat * w
        lng_sum += f.lng * w
    return lat_sum / total, lng_sum / total


def _max_radius_m(fixes: list[RawFix], lat: float, lng: float) -> float:
    if not fixes:
        return 0.0
    return max(haversine_distance_m(f.lat, f.lng, lat, lng) for f in fixes)


def mode_address(values: Iterable[str | None]) -> str | None:
    """The MODE — most frequent non-None value — of a stream of addresses,
    fail-closed (R-5 posture): `None` when the stream is empty or every
    value is `None`, never a guess built from partial data.

    Ties break by FIRST OCCURRENCE in `values` iteration order. Both
    callers feed an order that already means something — `segment_stays`'
    fixes are ts-ascending and the rollup's cluster members are
    started_ts-ascending — so "first" is "earliest observed", which makes
    the answer deterministic in both and independent of hash or heap
    order. `None` is excluded before counting, not counted as a candidate:
    an address-less fix is ABSENCE of a reading, never a value that could
    win or force a tie (the fallback fills labels, it must not dilute them).

    One implementation on purpose: this same rule names a `Stay` (from its
    fixes) and a derived place (from its stays' `Stay.address` values) —
    two copies would drift, and the place label is user-visible state.
    """
    counts: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    # `max` returns the FIRST maximal element it meets, and a dict iterates
    # in insertion (first-occurrence) order — one pass, and the tie-break
    # rule above falls out of the data structure rather than a second pass
    # over a separately-tracked first-seen index.
    return max(counts, key=lambda v: counts[v])


def _close(
    group: list[RawFix], dwells: list[int], *,
    subject_id: str, person_id: str, circle_id: str, min_dwell_ms: int,
    wander: list[RawFix] | None = None,
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
    wander = wander or []
    ended_ts = max([group[-1].ts, *(f.ts for f in wander)])
    total_dwell = ended_ts - group[0].ts
    if total_dwell < min_dwell_ms:
        return None
    lat, lng = _dwell_weighted_centroid(group, [*dwells, 0])
    return Stay(
        subject_id=subject_id, person_id=person_id, circle_id=circle_id,
        started_ts=group[0].ts, ended_ts=ended_ts,
        lat=lat, lng=lng, radius_m=_max_radius_m(group, lat, lng),
        fix_count=len(group) + len(wander),
        # The group's mode Life360 address rides on the
        # stay. The fixes that produced it are behind the stay-side
        # watermark once this stay commits, so segmentation is the only
        # moment the rollup can ever see their addresses.
        address=mode_address(f.address for f in group),
    )


def collapse_duplicate_fixes(fixes: list[RawFix]) -> list[RawFix]:
    """One fix per `(ts, lat, lng)`, keeping the LOWEST id, in input order.

    A person in two Life360 circles is recorded once per circle: the same
    reading, same timestamp, same position, two `presence_fixes` rows (the
    store's key is `(circle, member, ts)`). The copies carry no
    information, and they defeat `segment_stays`' spike rule outright — a
    GPS spike is always followed by its own copy, so «the next fix is back
    inside the radius» is never true and every spike ended the stay. They
    also double `fix_count` and inflate `extract_trips`' fix counts.

    The lowest id is kept so a caller's watermark hold, computed from the
    kept fixes' ids, still re-reads every copy."""
    kept: dict[tuple[int, float, float], RawFix] = {}
    for f in fixes:
        key = (f.ts, f.lat, f.lng)
        if key not in kept or f.id < kept[key].id:
            kept[key] = f
    seen: set[tuple[int, float, float]] = set()
    out: list[RawFix] = []
    for f in fixes:
        key = (f.ts, f.lat, f.lng)
        if key in seen:
            continue
        seen.add(key)
        out.append(kept[key])
    return out


#: Below the radius by at least this much, the triangle-inequality bound in
#: `_RunningGroup.fits` is conclusive; within it, the exact scan decides. A
#: millimetre is orders above haversine's rounding at these distances, so the
#: fast answer is always the one the scan would give.
_FIT_MARGIN_M = 1e-3


class _RunningGroup:
    """The group `segment_stays` is growing, with the dwell-weighted sums of
    the members whose weight is final (every member but the newest, whose
    weight is the dwell into whatever comes next).

    `fits(candidate, into)` answers EXACTLY what
    `_max_radius_m([*members, candidate], *_dwell_weighted_centroid([*members,
    candidate], [*weights, into, 0])) <= radius_m` answers, in O(1) for the
    common case instead of rescanning every member: the centroid comes from
    the running sums (the same left fold `_dwell_weighted_centroid` computes,
    so the same bits), and «every member within the radius of the new
    centroid» is proved from a reference point by the triangle inequality
    (`ref_r + |ref, centroid| <= radius - margin`) before any scan; only an
    inconclusive bound rescans, and the rescan resets the reference. A group
    that stays on one spot for hours — a night at home, the fold's held
    window — is what made the scan quadratic per evaluation."""

    __slots__ = (
        "lat_sum", "lng_sum", "members", "radius_m", "ref", "ref_r", "w_sum",
        "weighted", "weights",
    )

    def __init__(self, first: RawFix, *, radius_m: float) -> None:
        self.members: list[RawFix] = [first]
        self.weights: list[int] = []
        self.lat_sum = 0.0
        self.lng_sum = 0.0
        self.w_sum = 0
        #: `any(weights)`: a zero sum is not the same thing when a weight is
        #: negative (a caller that broke the ts-ascending contract).
        self.weighted = False
        self.radius_m = radius_m
        self.ref: tuple[float, float] | None = None
        self.ref_r = 0.0

    def centroid(self) -> tuple[float, float]:
        """`_dwell_weighted_centroid(members, [*weights, 0])`."""
        if not self.weighted:
            return _dwell_weighted_centroid(self.members, [*self.weights, 0])
        last = self.members[-1]
        total = self.w_sum or 1
        return (
            (self.lat_sum + last.lat * 0) / total,
            (self.lng_sum + last.lng * 0) / total,
        )

    def _centroid_with(self, candidate: RawFix, into: int) -> tuple[float, float]:
        if not self.weighted and into == 0:
            return _dwell_weighted_centroid(
                [*self.members, candidate], [*self.weights, into, 0],
            )
        last = self.members[-1]
        total = (self.w_sum + into) or 1
        lat = (self.lat_sum + last.lat * into) + candidate.lat * 0
        lng = (self.lng_sum + last.lng * into) + candidate.lng * 0
        return lat / total, lng / total

    def fits(self, candidate: RawFix, into: int) -> bool:
        c_lat, c_lng = self._centroid_with(candidate, into)
        own = haversine_distance_m(candidate.lat, candidate.lng, c_lat, c_lng)
        if own > self.radius_m:
            return False
        if self.ref is not None:
            shift = haversine_distance_m(self.ref[0], self.ref[1], c_lat, c_lng)
            if self.ref_r + shift <= self.radius_m - _FIT_MARGIN_M:
                return True
        worst = max(haversine_distance_m(f.lat, f.lng, c_lat, c_lng) for f in self.members)
        self.ref = (c_lat, c_lng)
        self.ref_r = worst
        return max(worst, own) <= self.radius_m

    def accept(self, candidate: RawFix, into: int) -> None:
        last = self.members[-1]
        self.lat_sum = self.lat_sum + last.lat * into
        self.lng_sum = self.lng_sum + last.lng * into
        self.w_sum += into
        self.weighted = self.weighted or into != 0
        self.weights.append(into)
        self.members.append(candidate)
        if self.ref is not None:
            self.ref_r = max(
                self.ref_r,
                haversine_distance_m(candidate.lat, candidate.lng, self.ref[0], self.ref[1]),
            )


def segment_stays(
    fixes: list[RawFix], *, subject_id: str, person_id: str, circle_id: str,
    radius_m: float, min_dwell_ms: int, wander_radius_m: float | None = None,
) -> SegmentationResult:
    """Stay-point detection: roll consecutive fixes into a
    running group while every fix seen so far stays within `radius_m` of the
    group's dwell-weighted centroid; the fix that would break that bound
    starts a new group instead. A group closes into a `Stay` only once its
    span reaches `min_dwell_ms` (the 120m/600s defaults are inherited, not
    measured; both are config knobs).

    `fixes` must already be ts-ascending and pre-filtered (accuracy_m <=
    100, wire_state == 'live' — the store's job); this function does
    not re-filter, per "interpret once."

    `wander_radius_m`: a fix
    outside `radius_m` but within `wander_radius_m` of the group's centroid,
    and not reporting `is_driving=True`, is WANDERING — it extends the stay
    in time but never moves its position or radius. Resting fixes at one real
    stop can spread far from its centre while Life360 reports the same
    `accuracy_m` for all of them, so the accuracy gate filters none: with
    only the `radius_m` test, a long stop in a dense area never becomes a
    stay and its minutes are written as a string of short "trips". `None`
    keeps the plain radius test.
    """
    if not fixes:
        return SegmentationResult(closed=(), open_stay=None)

    closed: list[Stay] = []
    group = _RunningGroup(fixes[0], radius_m=radius_m)
    wander: list[RawFix] = []

    def _wanders(candidate: RawFix) -> bool:
        # Only an ESTABLISHED stop can be wandered from: two core fixes, the
        # first not a driving reading. Otherwise one arrival fix — or the
        # last fix of a drive — becomes a stop's centre and the next fix
        # within `wander_radius_m` pins a stay on the road.
        if (
            wander_radius_m is None or candidate.is_driving is True
            or len(group.members) < 2 or group.members[0].is_driving is True
        ):
            return False
        c_lat, c_lng = group.centroid()
        return haversine_distance_m(c_lat, c_lng, candidate.lat, candidate.lng) <= wander_radius_m

    i = 1
    while i < len(fixes):
        candidate = fixes[i]
        into = dwell_ms(group.members[-1].ts, candidate.ts)
        if group.fits(candidate, into):
            group.accept(candidate, into)
            i += 1
            continue
        if _wanders(candidate):
            wander.append(candidate)
            i += 1
            continue
        # A SINGLE-FIX SPIKE does not end a stay. At typical admitted
        # accuracy (50 m median, 100 m cap), one scattered fix lands outside
        # the radius routinely: scatter fills the bound. Closing on it would
        # drop the spike as a one-fix group and open a SECOND stay at the same
        # place, leaving a hole inside one real stay that every
        # duration/arrival statistic above it would read.
        # A spike is a candidate outside the radius whose NEXT fix is back
        # inside it; it is skipped (not merged into the centroid) and the
        # group continues. Two consecutive outside fixes are a departure, and
        # so is a return that comes after more than `DWELL_CAP_MS` of
        # silence: the bridge only spans a gap the resting cadence itself
        # could produce (the p99 within-stay gap is ~30.5 min), never an
        # unobserved absence of hours.
        if i + 1 < len(fixes):
            after = fixes[i + 1]
            bridge = dwell_ms(group.members[-1].ts, after.ts)
            # Each gap is bounded on its own: bounding the two together by one
            # DWELL_CAP_MS would make the rule dead at any resting cadence
            # slower than ~16 min, i.e. at rest, exactly where spikes are.
            if (
                candidate.ts - fixes[i - 1].ts <= DWELL_CAP_MS
                and after.ts - candidate.ts <= DWELL_CAP_MS
                and group.fits(after, bridge)
            ):
                group.accept(after, bridge)
                i += 2
                continue
        elif len(group.members) > 1:
            # The breaking fix is the LAST one in the window, so whether it
            # is a spike or a departure is not yet observable. Leave the
            # group OPEN (the rollup holds its fixes readable) rather than
            # closing it on a fix the next window may show to be noise; the
            # undecided fix is covered by no stay this window.
            break
        # `candidate` breaks the radius test — close what came before it
        # (if it reached min_dwell_ms) and start a new group at `candidate`.
        # `group.weights` are the group's own internal transitions only —
        # the transition INTO `candidate` does not belong to it.
        stay = _close(
            group.members, list(group.weights), subject_id=subject_id,
            person_id=person_id, circle_id=circle_id, min_dwell_ms=min_dwell_ms,
            wander=wander,
        )
        if stay is not None:
            closed.append(stay)
        group = _RunningGroup(candidate, radius_m=radius_m)
        wander = []
        i += 1

    # Whatever remains in `group` never saw a breaking fix within this
    # window — it is the open stay, not a closed one (the rollup's fold
    # decides closeability on its logical clock).
    members = group.members
    open_lat, open_lng = _dwell_weighted_centroid(members, [*group.weights, 0])
    open_stay = Stay(
        subject_id=subject_id, person_id=person_id, circle_id=circle_id,
        started_ts=members[0].ts,
        ended_ts=max([members[-1].ts, *(f.ts for f in wander)]),
        lat=open_lat, lng=open_lng,
        radius_m=_max_radius_m(members, open_lat, open_lng),
        fix_count=len(members) + len(wander),
        # Same as `_close`: an open stay closes later by radius break (the
        # non-final-chunk path) or by the fold's clock (the stay-hold path)
        # — both land it in `new_stays`, and the place-label fallback must
        # reach its address too, not just closed-with-the-group ones.
        address=mode_address(f.address for f in members),
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
    """DBSCAN over stay centroids, haversine metric. Hand-rolled
    rather than scikit-learn: at this scale the O(n^2) neighbor search below
    is trivial, and pulling in scipy for one function would be the opposite
    of YAGNI. Noise points
    (fewer than `min_samples` neighbors, and not reachable from a core
    point) are DROPPED, never returned as singleton clusters — DBSCAN's own
    definition, and the reason a subject with too few stays anywhere yields
    no places at all rather than a low-confidence one.

    Avoids the three usual hand-rolled-DBSCAN failure modes
    (a border point left unlabeled instead of folded into its core
    point's cluster; the expansion loop failing to re-visit and relabel a
    point that was marked noise before its reaching core point was itself
    discovered; non-contiguous or merged cluster ids across 3+ clusters).
    `labels[j] == -1` catches exactly that
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
# how 0.3 was chosen (a safety margin above the ambiguous ~0.22-0.24 shares).
_HOME_NIGHT_SHARE_MIN = 0.3

# `is_home`'s ABSOLUTE distinct-night gate. The share floor above is mathematically unreachable for
# WFH / stay-at-home subjects: they are home all day, so `total_dwell` grows with every hour of
# daytime presence while the night window (1:00-5:00) is capped at 4h/day —
# night_share tops out at ~0.167 for a pure stay-at-home, far below 0.3.
# Genuine homes of such subjects show 18-29 distinct nights yet fail the share
# gate, while a one-off trip cluster has only ~4 nights and must NOT label. 8
# sits between the false-positive floor (4) and the weakest genuine home (18).
_HOME_MIN_NIGHTS_ABS = 8

# `is_work`'s floor on absolute work-window dwell (a share alone has no
# notion of scale). One workday's worth of dwell — comfortably above
# the ~2-3h a handful of incidental weekday-daytime visits accumulate, and
# comfortably below any real workplace's multi-week total.
_MIN_WORK_DWELL_MS = 8 * 3_600_000


@dataclass(frozen=True)
class LabelResult:
    """`kind` is `'home'` / `'work'` / `'unlabeled'` — never a guess.
    `most_recent_support_ts` is the latest
    local night-or-weekday timestamp that contributed to a non-unlabeled
    kind; `None` for `unlabeled`. It exists so the ROLLUP can
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


def hour_of_week(ts: int, tz_name: str) -> int:
    """Which local hour-of-week bucket ONE moment falls in (0..167, Monday
    00:00 = 0) — the single-timestamp sibling of `hour_of_week_bins`, and
    Tier 5's speed-percentile grouping key.

    A trip is bucketed by its `started_ts` alone, deliberately: unlike a
    stay, whose whole point is the dwell it spreads across buckets, a
    trip's `avg_speed_kmh` is ONE number for the whole span and cannot be
    apportioned across the hours it crosses without inventing a
    within-trip speed profile nothing measured. Splitting it with
    `hour_of_week_bins` would look more careful and be less grounded.

    LOCAL clock, never `ts // 3_600_000 % 168` — the epoch's own week
    origin is a Thursday, and HKT is UTC+8, so the arithmetic shortcut is
    wrong on both axes at once."""
    local = datetime.fromtimestamp(ts / 1000, tz=ZoneInfo(tz_name))
    return local.weekday() * 24 + local.hour


def hour_of_week_bins(started_ts: int, ended_ts: int, tz_name: str) -> dict[int, int]:
    """A stay's dwell, split into local hour-of-week buckets (0..167,
    Monday 00:00 = 0) — Tier 3's occupancy input.

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
    """Fail-closed, recency-gated labelling. Both shares are
    TIME-WEIGHTED overlap with a fixed local-clock window, never a count of
    stays — a cluster's `home`/`work` status is about how long someone
    dwelled there at which hours, not how many separate visits it took.

    A label additionally requires its most recent supporting night/weekday
    to fall within `label_freshness_days` of `now_ms` — without this, the
    rule is a pure function of all-time totals and the rollup's
    `max_age_seconds` would have nothing to recompute at zero lag.

    The day-walk in `_windowed_dwell` attributes an overnight stay's
    night-window overlap to the LATER calendar day (a 23:00->07:00 stay's
    01:00-05:00 overlap lands on the second day, not the first); the weekday
    gate SKIPS a weekend day's window computation entirely rather than
    computing and discarding it.

    **Why `is_home`'s floor is `_HOME_NIGHT_SHARE_MIN` (0.3), not 0.5**:
    `night_share`'s numerator is capped at 4h (the night window's own width)
    no matter how long someone actually stays, while its denominator
    (`total_dwell`) grows with every hour of ANY dwell at the cluster — so
    `night_share` for someone truly home overnight falls as their honest
    daytime/evening presence there grows, not as their "home-ness" falls. A
    0.5 floor requires an overnight stay to be 8h (4 / 0.5) or SHORTER —
    unreachable for the common case of someone home 22:00-08:00 (10h,
    `night_share = 0.4`) or longer. 0.3 makes clean overnight patterns up to
    `4 / 0.3 ≈ 13.3h` reachable while staying above the genuinely ambiguous
    cells (`night_share ≈ 0.22-0.235`), which carry substantial dwell in the
    working window too — exactly the case fail-closed labelling exists to
    catch.

    Scoping the denominator to a "plausible home span" (e.g. 20:00-10:00)
    does not help: no window width separates an ambiguous cell from a
    full-time WFH subject. The WFH subject's daytime dwell dilutes
    `night_share` under any window wide enough to catch it, while a narrower
    window pushes the ambiguous cell's share closer to 1.0. Keeping the
    ambiguous cell excluded is the non-negotiable constraint (a real home
    mislabelled `unlabeled` is a far smaller cost than an ambiguous cell
    mislabelled `home`), so the denominator is `total_dwell` (all hours).
    The WFH case is carried by the absolute distinct-night gate
    (`_HOME_MIN_NIGHTS_ABS`) instead.

    **Why `is_work` has `_MIN_WORK_DWELL_MS`**: a share (`work_share >= 0.5`)
    alone has no notion of scale — a location visited for 7 brief stays
    totalling ~3h across 5 weekday afternoons can clear `work_share >= 0.5`
    and `len(work_days) >= min_weekdays` exactly the way a real 55h/week
    office does. `_MIN_WORK_DWELL_MS` (8h, one workday) is an absolute floor
    under `work_dwell` that rejects the former.

    The explicit `is_home and is_work` check is REACHABLE: `is_home`'s
    absolute night-days arm (`len(night_days) >= _HOME_MIN_NIGHTS_ABS`) does
    not share the `night_share` variable with `is_work`'s `night_share < 0.1`
    term, so a cluster with 8+ distinct nights of small dwell AND a large
    weekday work-window dwell can satisfy both. That genuinely ambiguous
    signal (sleeps there most nights, works there most weekdays) fail-closes
    to `unlabeled` — never pick one arbitrarily."""
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
    ) or (
        # Absolute arm: WFH / stay-at-home subjects can
        # never clear the share floor (see `_HOME_MIN_NIGHTS_ABS`), so a
        # high distinct-night count is accepted as home evidence on its own.
        len(night_days) >= _HOME_MIN_NIGHTS_ABS
    )
    is_work = (
        work_share >= 0.5
        and night_share < 0.1
        and work_dwell >= _MIN_WORK_DWELL_MS
        and len(work_days) >= min_weekdays
    )
    if is_home and is_work:
        # A cluster cannot be both — carrying substantial dwell in BOTH
        # windows is exactly the ambiguous signal
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
        # The stale-label failure to avoid: evidence exists but is
        # too old relative to a `now_ms` that keeps moving with no new fixes.
        return LabelResult(kind="unlabeled", confidence=0.0, most_recent_support_ts=None)

    return LabelResult(
        kind="home" if is_home else "work",
        confidence=confidence,
        most_recent_support_ts=support,
    )


# ---------------------------------------------------------------------------
# Place identity — DEC-190 (ADR 0014).
# ---------------------------------------------------------------------------

# Two Life360 saved places closer than this describe one place (duplicates
# in two different circles split one residence's history near-evenly). The merge is deliberately
# NOT scoped per circle.
TAG_MERGE_RADIUS_M = 100.0


@dataclass(frozen=True)
class TaggedPlace:
    """One Life360 saved place (`presence_places`) — a HUMAN-named, stable-id
    registry entry. Trusted for identity and name, never for geometry: a pin
    can sit hundreds of metres from the cluster it names."""

    circle_id: str
    place_id: str
    name: str
    lat: float
    lng: float
    radius_m: float | None


@dataclass(frozen=True)
class DerivedPlace:
    """One stored `presence_derived_places` row. `kind` is an ATTRIBUTE of a
    place (DEC-190), not its key: `unlabeled` is a stored value, and a subject
    may hold any number of places, two homes included."""

    id: int
    subject_id: str
    circle_id: str
    lat: float
    lng: float
    radius_m: float
    kind: Literal["home", "work", "unlabeled"]
    confidence: float
    label: str | None
    saved_place_id: str | None
    first_seen_ts: int = 0
    last_seen_ts: int = 0


@dataclass(frozen=True)
class PlannedPlace:
    """One place the reconcile says should exist. `existing_id` is the stored
    row it keeps the identity of (`None` = insert a new row). `stays` is every
    stay that resolves to it — the ONLY source of its geometry (a tagged place
    never takes the pin's coordinates)."""

    existing_id: int | None
    lat: float
    lng: float
    radius_m: float
    label: LabelResult
    saved_place_id: str | None
    tag_name: str | None
    stays: tuple[Stay, ...]


def _stay_dwell(stay: Stay) -> int:
    return max(0, stay.ended_ts - stay.started_ts)


def dwell_weighted_centroid(stays: Iterable[Stay]) -> tuple[float, float]:
    """The dwell-weighted centroid of a set of stays (equal weights when none
    carries dwell). One function for every caller that places a place, so a
    place's stored coordinates and the distance its stays are checked against
    can never be computed two ways."""
    members = list(stays)
    weights = [_stay_dwell(s) for s in members]
    if not any(weights):
        weights = [1] * len(members)
    total = sum(weights)
    lat = sum(s.lat * w for s, w in zip(members, weights, strict=True)) / total
    lng = sum(s.lng * w for s, w in zip(members, weights, strict=True)) / total
    return lat, lng


def _merge_tags(tagged: Iterable[TaggedPlace]) -> list[list[TaggedPlace]]:
    """Single-linkage groups of tags within `TAG_MERGE_RADIUS_M`, across
    circles. Each group's FIRST member (sorted by `(circle_id, place_id)`) is
    its representative — a deterministic choice, so the identity a merged
    group keeps does not depend on the order Life360 or Postgres returned
    rows in."""
    ordered = sorted(tagged, key=lambda t: (t.circle_id, t.place_id))
    groups: list[list[TaggedPlace]] = []
    for tag in ordered:
        joined = [
            g for g in groups
            if any(
                haversine_distance_m(tag.lat, tag.lng, m.lat, m.lng)
                <= TAG_MERGE_RADIUS_M
                for m in g
            )
        ]
        merged = [tag]
        for g in joined:
            merged.extend(g)
            groups.remove(g)
        groups.append(sorted(merged, key=lambda t: (t.circle_id, t.place_id)))
    groups.sort(key=lambda g: (g[0].circle_id, g[0].place_id))
    return groups


def _nearest_tag_group(
    lat: float, lng: float, groups: list[list[TaggedPlace]], *,
    match_radius_m: float, use_tag_radius: bool,
) -> int | None:
    best: tuple[float, int] | None = None
    for gi, group in enumerate(groups):
        for tag in group:
            d = haversine_distance_m(lat, lng, tag.lat, tag.lng)
            limit = match_radius_m
            if use_tag_radius and tag.radius_m is not None:
                limit = max(limit, tag.radius_m)
            if d <= limit and (best is None or (d, gi) < best):
                best = (d, gi)
    return None if best is None else best[1]


def plan_places(
    stays: list[Stay], *, existing: list[DerivedPlace],
    tagged: list[TaggedPlace], now_ms: int, tz_name: str,
    cluster_eps_m: float, cluster_min_samples: int, match_radius_m: float,
    min_nights: int, min_weekdays: int, label_freshness_days: int,
) -> list[PlannedPlace]:
    """The whole place picture for ONE subject, recomputed from its stays —
    DEC-190's four rules in order:

    1. a stay within `max(tag radius, match_radius_m)` of a tagged place
       resolves to it;
    2. a tagged place is a place as soon as one stay resolves to it — no
       clustering, no `min_nights`;
    3. a DBSCAN cluster of the remaining stays whose centroid is within
       `match_radius_m` of a tagged place joins it rather than rivalling it;
    4. otherwise the cluster is its own place — unbounded per subject, with
       `home`/`work`/`unlabeled` as a label computed from its own stays.

    **Identity survives by stay OVERLAP, not by stored coordinates.** Each
    planned place keeps the id of the stored row its stays are currently
    linked to (dwell-weighted, greedy, one row per place), with a matching
    `saved_place_id` outranking overlap and proximity within
    `match_radius_m` as the last resort. Coordinates cannot be the key: a
    stored row's position is exactly what cannot be trusted, since rows can
    drift onto other clusters. Overlap hands the id to the cluster that owns the
    links and gives the displaced cluster a fresh row — which is the repair.

    Stays in no planned place are noise (DBSCAN's own definition) and the
    caller unlinks them. Pure: no store, no clock beyond `now_ms`."""
    tag_groups = _merge_tags(tagged)
    by_tag: dict[int, list[Stay]] = {}
    untagged: list[Stay] = []
    for stay in stays:
        gi = _nearest_tag_group(
            stay.lat, stay.lng, tag_groups,
            match_radius_m=match_radius_m, use_tag_radius=True,
        )
        if gi is None:
            untagged.append(stay)
        else:
            by_tag.setdefault(gi, []).append(stay)

    derived_clusters: list[list[Stay]] = []
    for cluster in cluster_places(
        untagged, eps_m=cluster_eps_m, min_samples=cluster_min_samples,
    ):
        c_lat, c_lng = dwell_weighted_centroid(cluster)
        gi = _nearest_tag_group(
            c_lat, c_lng, tag_groups,
            match_radius_m=match_radius_m, use_tag_radius=False,
        )
        if gi is None:
            derived_clusters.append(cluster)
        else:
            by_tag.setdefault(gi, []).extend(cluster)

    drafts: list[tuple[list[Stay], TaggedPlace | None]] = [
        (sorted(members, key=lambda s: s.started_ts), tag_groups[gi][0])
        for gi, members in sorted(by_tag.items())
    ]
    drafts.extend(
        (sorted(c, key=lambda s: s.started_ts), None) for c in derived_clusters
    )

    # Identity: greedy over (saved-id match, linked dwell, -distance).
    existing_by_id = {p.id: p for p in existing}
    candidates: list[tuple[tuple[int, int, float], int, int]] = []
    centroids = [dwell_weighted_centroid(members) for members, _ in drafts]
    for di, (members, tag) in enumerate(drafts):
        overlap: dict[int, int] = {}
        for s in members:
            if s.place_id is not None and s.place_id in existing_by_id:
                overlap[s.place_id] = overlap.get(s.place_id, 0) + max(1, _stay_dwell(s))
        for place in existing:
            saved = int(tag is not None and place.saved_place_id == tag.place_id)
            linked = overlap.get(place.id, 0)
            dist = haversine_distance_m(
                centroids[di][0], centroids[di][1], place.lat, place.lng,
            )
            if saved or linked or dist <= match_radius_m:
                candidates.append(((saved, linked, -dist), di, place.id))
    candidates.sort(key=lambda c: (c[0], -c[1], -c[2]), reverse=True)
    assigned_draft: dict[int, int] = {}
    used: set[int] = set()
    for _score, di, pid in candidates:
        if di in assigned_draft or pid in used:
            continue
        assigned_draft[di] = pid
        used.add(pid)

    planned: list[PlannedPlace] = []
    for di, (members, tag) in enumerate(drafts):
        lat, lng = centroids[di]
        radius = max(
            [cluster_eps_m]
            + [haversine_distance_m(lat, lng, s.lat, s.lng) for s in members]
        )
        planned.append(PlannedPlace(
            existing_id=assigned_draft.get(di),
            lat=lat, lng=lng, radius_m=radius,
            label=label_place(
                members, now_ms=now_ms, tz_name=tz_name, min_nights=min_nights,
                min_weekdays=min_weekdays,
                label_freshness_days=label_freshness_days,
            ),
            saved_place_id=None if tag is None else tag.place_id,
            tag_name=None if tag is None else tag.name,
            stays=tuple(members),
        ))
    return planned
