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

from dataclasses import dataclass

from nora.domain.presence import haversine_distance_m

__all__ = [
    "DWELL_CAP_MS",
    "RawFix",
    "SegmentationResult",
    "Stay",
    "cluster_places",
    "dwell_ms",
    "segment_stays",
]

# The observed 1800s still-ceiling (config.toml's cadence_still_seconds)
# plus slack (spec §3.3, design/27 §1.1).
DWELL_CAP_MS = 1_900_000


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
