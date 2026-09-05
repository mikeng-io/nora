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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

from nora.domain.presence import haversine_distance_m

__all__ = [
    "DWELL_CAP_MS",
    "CHUNK_GAP_MS",
    "LabelResult",
    "RawFix",
    "SegmentationResult",
    "Stay",
    "Trip",
    "best_corridor_match",
    "circular_mean_minute",
    "cluster_places",
    "cluster_trips",
    "derive_speed_kmh",
    "dwell_ms",
    "edit_distance",
    "extract_trips",
    "hour_of_week",
    "hour_of_week_bins",
    "label_place",
    "mode_address",
    "robust_z_score",
    "segment_stays",
    "trailing_fix_is_settled",
]

# The observed 1800s still-ceiling (config.toml's cadence_still_seconds)
# plus slack (spec §3.3, design/27 §1.1).
DWELL_CAP_MS = 1_900_000

# The batch-split threshold for `_chunk_by_gap` (rollup Speed 1) — how long a
# gap between two consecutive fixes must be before they are treated as
# SEPARATE visits rather than one continuous presence. Deliberately a
# CROSS-DAY span, NOT the same 31.7-min `DWELL_CAP_MS` dwell ceiling:
# Life360's resting cadence is one fix every 10-55 minutes, so a subject
# who stays home all day (WFH) produces fixes 30+ minutes apart that a
# 31.7-min split would carve into dozens of sub-min-dwell fragments — the
# production shape that left miles_novak with 195 home stays instead of one
# 14.7h stay (2026-09-05 live-data finding). A 12h gap separates genuine
# day-to-day returns (the original reason the split exists) while keeping
# an all-day home presence in ONE chunk. `dwell_ms`'s own cap stays
# `DWELL_CAP_MS` — that is a still-ceiling for how long a single fix's
# position may be credited, a different job from batch partitioning.
CHUNK_GAP_MS = 12 * 3_600_000

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


def edit_distance(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    """Classic Levenshtein distance over two corridor address sequences —
    Tier 5's trip-similarity metric (design/27 §4, issue #6: "cluster
    trips by address-sequence edit distance"). Hand-rolled, O(len(a) *
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
    identically and they previously did not: each carried its own copy of
    this loop, `SqlMobilityStore` resolving a tie on whatever order
    Postgres happened to return its heap in and `FakeMobilityStore` on dict
    insertion order (final whole-branch review, Important #3). Two
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
    # Trip settlement (design/27 §4, 2026-08-20 trip-settlement design):
    # Life360's own per-fix device signal, 100% filled — the PRIMARY
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
    # Issue #10 option 1 (derived-place address labels): the MODE of this
    # stay's fixes' `address` — Life360's own free place string, already
    # 100% filled (design/27 §1) — chosen once at segmentation time via
    # `mode_address` below (most frequent non-None, first occurrence on a
    # tie, `None` when every fix lacked an address). This is the in-memory
    # input for the rollup's place-label FALLBACK (issue #10): the Google
    # geocode cache stays canonical whenever it has a hit, and this field
    # only fills the gap when it does not. Deliberately NOT persisted —
    # `presence_stays` has no address column, so a stay re-read from the
    # store carries `None` and `run_once` re-attaches this cycle's
    # addresses from its own segmentation output instead of trusting the
    # roundtrip.
    address: str | None = None
    # Tier 3 (design/27 §4, issue #6): set once this stay's centroid is
    # found inside an existing home/work `presence_derived_places` row —
    # `MobilityRollup._record_rhythm` (Task 10) is the only writer. `None`
    # means either "not yet checked" or "outside every labelled place" —
    # the two are indistinguishable here on purpose, since a stay that is
    # outside every place today may fall inside one created LATER (Task 10's
    # backfill step handles that case going forward, not this field).
    place_id: int | None = None


@dataclass(frozen=True)
class Trip:
    """One movement span between stays (design/27 §4, issue #6) — the
    complement of `Stay`: fixes NOT absorbed into any stay-forming group.
    `distance_m`/`avg_speed_kmh` are derived from the trip's OWN fixes
    (`derive_speed_kmh` pairwise, summed), never from Life360's
    `speed_kmh`. `corridor` is the deduplicated address sequence — the
    corridor-CLUSTERING input (Task 6); `corridor_id` is set once
    clustered (Task 8), `None` until then, the same nullable-then-filled
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
    say that group has stopped moving? (The 2026-08-20 trip-settlement
    design's `_is_settled`, §2.2.)

    `is_driving is True` is Life360 saying movement is ongoing, so no
    amount of elapsed wall-clock settles it. `False` is Life360 saying it
    stopped, which settles immediately. `None` is **no reading at all** —
    a nullable column, and the real state of every historical fix and
    anything Life360 omits the flag on — and the only honest fallback
    there is the same quiet-boundary clock Tier 1 already uses for a
    stay's arrival. A `None` fix must never be treated as settled just
    because it isn't `True`: that is the original fragmentation bug with
    the signal simply absent.

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
    trips — Tier 5's segmentation counterpart to `segment_stays` (design/27
    §4, issue #6).

    `fixes` and `closed_stays` come from the SAME batch `segment_stays`
    already processed this cycle (`nora.analytics.rollup`'s Speed 1) —
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
    unless `now_ms` says otherwise (2026-08-20 trip-settlement design,
    §2.2/§2.3): settled when its last fix's `is_driving` is `False`, or
    (when `is_driving is None`) once `settled_seconds` has elapsed since
    that fix, or unconditionally once `max_trip_duration_seconds` is
    exceeded (the stuck-signal safety valve). Returns `(trips,
    held_back)` — `held_back` carries the undecided tail's own fixes so
    the caller can hold the watermark back rather than losing them.
    `now_ms=None` (the default, what a non-final chunk passes) skips the
    settlement check entirely and reproduces the ORIGINAL always-flush
    behavior byte-for-byte — this is what keeps non-final chunks and
    every existing caller unaffected."""
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
                    # basically anything is `>= 0`), silently reintroducing
                    # the original fragmentation bug for exactly the fixes
                    # this fallback exists to protect. `inf` means "never
                    # sufficient" instead. No current caller actually omits
                    # `settled_seconds` while supplying `now_ms` (both real
                    # call sites always pass both together), so this is
                    # belt-and-braces, not a behavior change.
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
    """DBSCAN over trip corridor sequences, edit-distance metric
    (design/27 §4, issue #6). Same algorithm shape as `cluster_places`
    (noise points below `min_samples` are DROPPED, never a singleton
    cluster), deliberately NOT sharing code with it — a separate,
    parallel implementation (task 6's own brief): `cluster_places` is
    independently verified against 70,000 randomized point sets, and
    refactoring it into a generic metric-parameterized version under this
    plan's time budget risks that verification for the ~20 lines it would
    save (CLAUDE.md's own "three similar lines is better than a premature
    abstraction").

    **Callers MUST hold out trips with an empty `corridor` before calling
    this.** An address-less trip (every fix in its group had no `address`)
    is NOT harmlessly self-segregating: `edit_distance((), x) == len(x)`,
    so an empty sequence is within a typical `max_edit_distance` of ANY
    short real corridor and this function will legitimately put it in the
    same cluster as three real, named trips. What happens next depends on
    input order and is wrong both ways round — judging the cluster by its
    first trip discards a whole real cluster when that trip is the empty
    one, and contaminates a real corridor's sample count and speed
    percentiles when it is not (reproduced in both directions in
    `tests/unit/test_analytics_rollup.py::
    test_an_address_less_trip_clustered_with_real_ones_is_held_out`; this
    docstring previously claimed the opposite and was simply false).

    Tier 5's own caller enforces the hold-out at the QUERY:
    `MobilityStore.unclustered_named_trips_for_subject` — the pool
    `MobilityRollup._record_corridors` clusters — filters
    `jsonb_array_length(sequence) > 0`, so an address-less trip never
    reaches here at all and ends its life with `corridor_id` NULL, the
    honest record of "no address, so no route". A future caller sourcing
    trips any other way owes itself the same filter.

    Checked by hand against the same three usual hand-rolled-DBSCAN
    failure modes `cluster_places`'s own docstring names (a border point
    left unlabeled instead of folded into its core point's cluster; the
    expansion loop failing to re-visit and relabel a point marked noise
    before its reaching core point was discovered; non-contiguous or
    merged cluster ids across 3+ clusters) —
    `test_cluster_trips_three_clusters_with_border_points_and_noise`
    reproduces all three in one fixture and was hand-verified against a
    deliberately-reintroduced version of the noise-relabelling bug before
    being committed."""
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
    win or force a tie (issue #10 option 1: the fallback fills labels, it
    must not dilute them).

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
        # Issue #10 option 1: the group's mode Life360 address rides on the
        # stay. The fixes that produced it are behind the stay-side
        # watermark once this stay commits, so segmentation is the only
        # moment the rollup can ever see their addresses.
        address=mode_address(f.address for f in group),
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
        # Same as `_close`: an open stay closes later by radius break (the
        # non-final-chunk path) or by the wall clock (`run_once`'s issue #80
        # stay-hold path) — both land it in `new_stays`, and the place-label
        # fallback must reach its address too, not just closed-with-the-group
        # ones.
        address=mode_address(f.address for f in group),
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

# `is_home`'s ABSOLUTE distinct-night gate (2026-09-05 total-solution fix).
# The share floor above is mathematically unreachable for WFH / stay-at-home
# subjects: they are home all day, so `total_dwell` grows with every hour of
# daytime presence while the night window (1:00-5:00) is capped at 4h/day —
# night_share tops out at ~0.167 for a pure stay-at-home, far below 0.3.
# Real corpus (2026-09-05): miles_novak 29 nights / delia_lindberg 24 / aaron_larsen 18 /
# grant_carver 19 all fail the share gate but are genuine homes; delia_lindberg's
# Thailand trip cluster has only 4 nights and must NOT label. 8 sits between
# the false-positive floor (4) and the weakest genuine home (18).
_HOME_MIN_NIGHTS_ABS = 8

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


def hour_of_week(ts: int, tz_name: str) -> int:
    """Which local hour-of-week bucket ONE moment falls in (0..167, Monday
    00:00 = 0) — the single-timestamp sibling of `hour_of_week_bins`, and
    Tier 5's speed-percentile grouping key (design/27 §4, issue #6).

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

    The explicit `is_home and is_work` check is REACHABLE since the
    2026-09-05 total-solution fix: `is_home`'s absolute night-days arm
    (`len(night_days) >= _HOME_MIN_NIGHTS_ABS`) no longer shares the
    `night_share` variable with `is_work`'s `night_share < 0.1` term, so a
    cluster with 8+ distinct nights of small dwell AND a large weekday
    work-window dwell can satisfy both. That genuinely ambiguous signal
    (sleeps there most nights, works there most weekdays) fail-closes to
    `unlabeled` — never pick one arbitrarily."""
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
        # Total-solution arm (2026-09-05): WFH / stay-at-home subjects can
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
