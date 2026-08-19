"""The mobility rollup — the tick, three fire conditions, three speeds
(docs/superpowers/specs/2026-08-18-presence-analytics-substrate-design.md
§4). `run_once` is per-subject and does the work; deciding WHEN to call it
for a given subject (the fire-condition evaluation against wall-clock
`settled_seconds`/`max_age_seconds`) is the caller's job — Task 9's
`__main__` loop, which is the only place that knows the real tick cadence
and can check every subject's watermark cheaply before deciding who to
call this tick.

`run_once` itself always does everything it can with what is available: it
segments every new fix it reads (Task 2), closes the open stay if it is old
enough for `settled_seconds` to have elapsed against `now_ms`, RECHECKS
labelling on every call regardless of whether clustering ran (the cheap
speed, spec §4.2), and re-clusters ONLY when the stay set actually changed
since `last_cluster_id` (the dirty-set marker, the expensive speed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from nora.domain.mobility import (
    DWELL_CAP_MS,
    LabelResult,
    RawFix,
    Stay,
    Trip,
    circular_mean_minute,
    cluster_places,
    cluster_trips,
    extract_trips,
    hour_of_week,
    hour_of_week_bins,
    label_place,
    robust_z_score,
    segment_stays,
)
from nora.domain.ports import GeocodeCacheStore
from nora.domain.presence import haversine_distance_m

log = logging.getLogger("nora.analytics.rollup")


class MobilityStore(Protocol):
    async def watermark(self, *, subject_id: str) -> tuple[int, int]: ...
    async def fixes_since(
        self, *, person_ids: list[str], after_fix_id: int, grace_ms: int,
        now_ms: int, limit: int,
    ) -> list[RawFix]: ...
    async def write_stays(self, stays: list[Stay]) -> None: ...
    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]: ...
    async def set_place_ids(
        self, *, subject_id: str, started_ts_in: list[int], place_id: int
    ) -> None: ...
    async def write_trips(self, trips: list[Trip]) -> None: ...
    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]: ...
    async def set_trip_corridor_ids(
        self, *, subject_id: str, started_ts_in: list[int], corridor_id: int
    ) -> None: ...
    async def existing_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], max_edit_distance: int,
    ) -> tuple[int, tuple[str, ...]] | None: ...
    async def upsert_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], sample_count: int,
        now_ms: int, existing_id: int | None,
    ) -> int: ...
    async def trip_speeds_for_corridor(
        self, *, subject_id: str, corridor_id: int,
    ) -> list[tuple[int, float]]: ...
    async def upsert_corridor_speed(
        self, *, subject_id: str, corridor_id: int, hour_of_week: int,
        p50_kmh: float, p90_kmh: float, sample_count: int, now_ms: int,
    ) -> None: ...
    async def existing_place(
        self, *, subject_id: str, kind: str
    ) -> tuple[int, float, float, float, LabelResult, str | None] | None: ...
    async def upsert_place(
        self, *, subject_id: str, circle_id: str, kind: str, lat: float,
        lng: float, radius_m: float, result: LabelResult, now_ms: int,
        label: str | None = None,
    ) -> None: ...
    async def advance_fix_watermark(self, *, subject_id: str, fix_id: int) -> None: ...
    async def advance_cluster_watermark(self, *, subject_id: str, fix_id: int) -> None: ...
    async def record_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
    ) -> None: ...
    async def stay_durations_for_place(
        self, *, subject_id: str, place_id: int
    ) -> list[int]: ...
    async def upsert_rhythm(
        self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
        mean_departure_minute: float, duration_median_ms: int,
        duration_mad_ms: int, sample_count: int, now_ms: int,
    ) -> None: ...


@dataclass(frozen=True)
class RollupConfig:
    tick_seconds: float
    safety_lag: int
    settled_seconds: float
    max_age_seconds: float
    batch_limit: int
    backfill_batch_limit: int
    backfill_threshold: int
    grace_seconds: float
    stay_radius_m: float
    stay_min_dwell_seconds: float
    cluster_eps_m: float
    cluster_min_samples: int
    min_nights: int
    min_weekdays: int
    label_freshness_days: int
    tz_name: str = "Asia/Hong_Kong"
    anomaly_min_samples: int = 5
    anomaly_z_threshold: float = 3.0
    corridor_max_edit_distance: int = 2
    corridor_min_samples: int = 3
    min_trip_fixes: int = 3


class MobilityRollup:
    def __init__(
        self, *, store: MobilityStore, config: RollupConfig, metrics: Any = None,
        geocode_cache: GeocodeCacheStore | None = None,
    ) -> None:
        self._store = store
        self._cfg = config
        self._metrics = metrics
        # Google-cache-only labelling (2026-08-19 follow-up, design/27 §3.5):
        # optional — a place with no nearby cached hit stays unlabelled
        # rather than triggering a proactive Google call, and a caller that
        # does not wire this behaves exactly as before it existed.
        self._geocode_cache = geocode_cache

    async def _label_for(self, *, lat: float, lng: float) -> str | None:
        if self._geocode_cache is None:
            return None
        try:
            return await self._geocode_cache.nearest_within(
                lat=lat, lng=lng, radius_m=self._cfg.cluster_eps_m,
            )
        except Exception:
            # The port promises never to raise; belt and braces so a store
            # outage costs a missing label, never a dropped cycle.
            log.exception("geocode_cache label lookup failed — leaving unlabelled")
            return None

    async def watermark(self, *, subject_id: str) -> tuple[int, int]:
        """Exposes the store's watermark read without leaking the store
        itself — `nora.analytics.__main__`'s tick loop uses this to decide
        whether a subject is worth calling `run_once` for, without reaching
        into a private attribute."""
        return await self._store.watermark(subject_id=subject_id)

    async def run_once(
        self, *, subject_id: str, person_ids: list[str], circle_id: str, now_ms: int,
    ) -> None:
        last_fix_id, last_cluster_id = await self._store.watermark(subject_id=subject_id)

        lag_probe = await self._store.fixes_since(
            person_ids=person_ids, after_fix_id=last_fix_id,
            grace_ms=int(self._cfg.grace_seconds * 1000), now_ms=now_ms,
            limit=self._cfg.backfill_batch_limit + 1,
        )
        over_threshold = len(lag_probe) > self._cfg.backfill_threshold
        batch_limit = (
            self._cfg.backfill_batch_limit if over_threshold else self._cfg.batch_limit
        )
        fixes = lag_probe[:batch_limit]

        if fixes:
            # `address=` is load-bearing from Tier 5 on (Task 2): it is the
            # ONLY input `extract_trips` has for a trip's corridor
            # sequence, and dropping it here silently produced empty
            # corridors that cluster with each other and nothing real.
            raw = [
                RawFix(id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address)
                for f in fixes
            ]
            min_dwell_ms = int(self._cfg.stay_min_dwell_seconds * 1000)
            # `segment_stays` is purely spatial (its own docstring): fixes at
            # (near-)identical coordinates never break its radius test, no
            # matter how large the time gap between them — so a subject who
            # returns to the same place night after night would otherwise
            # merge every visit into ONE pathologically long "stay" spanning
            # the away-time between them too. That merged stay's
            # `ended_ts - started_ts` (its recorded dwell, per `Stay`'s own
            # invariant) then includes all that dead time, which dilutes
            # `label_place`'s TIME-WEIGHTED night/work share below any
            # threshold — a subject who is unambiguously home every night
            # would never label as `home`. Splitting the batch first,
            # wherever the gap between two consecutive fixes exceeds
            # `DWELL_CAP_MS` (the same still-ceiling `dwell_ms` itself caps
            # against), and segmenting each chunk independently, is what
            # turns a genuine day-to-day return into the SEPARATE stays
            # `label_place` and `cluster_places` are both written and tested
            # against (see `tests/unit/test_mobility.py`'s label/cluster
            # fixtures, which construct discrete per-day `Stay` objects
            # directly rather than deriving them from a raw fix stream).
            new_stays: list[Stay] = []
            new_trips: list[Trip] = []
            chunks = _chunk_by_gap(raw, DWELL_CAP_MS)
            last_open_stay: Stay | None = None
            for i, chunk in enumerate(chunks):
                seg = segment_stays(
                    chunk, subject_id=subject_id,
                    person_id=person_ids[-1],  # most-recently-supplied id, for provenance
                    circle_id=circle_id,
                    radius_m=self._cfg.stay_radius_m,
                    min_dwell_ms=min_dwell_ms,
                )
                new_stays.extend(seg.closed)
                # Tier 5 (design/27 §4): the SAME chunks the stay
                # segmentation just walked — trips are the complement of
                # stays over one identical partition of the batch, so
                # re-chunking here could only produce a different answer,
                # never a better one.
                chunk_closed = list(seg.closed)
                open_started: int | None = None
                if seg.open_stay is not None:
                    if i == len(chunks) - 1:
                        # Only the FINAL chunk's trailing group is genuinely
                        # open — nothing has been observed yet to say the
                        # subject left; that is `settled_seconds`' job below.
                        # `extract_trips` must therefore stop at its first
                        # fix rather than call the tail a trip.
                        last_open_stay = seg.open_stay
                        open_started = seg.open_stay.started_ts
                    elif seg.open_stay.ended_ts - seg.open_stay.started_ts >= min_dwell_ms:
                        # A non-final chunk's trailing group ended because the
                        # NEXT chunk's first fix was far enough away in time —
                        # an observed gap, not a guess — so it closes now,
                        # subject to the same min-dwell gate `segment_stays`'
                        # own `_close` applies.
                        new_stays.append(seg.open_stay)
                        # ...and it is a CLOSED stay for this chunk's trip
                        # extraction too. `seg.closed` alone would omit it,
                        # and `extract_trips` would then read a genuine
                        # stay's own fixes as movement and write them as a
                        # ~0 km/h "trip" — the brief's own sketch had this
                        # bug; the sub-min-dwell case correctly stays out,
                        # since `segment_stays` discarded that group too.
                        chunk_closed.append(seg.open_stay)
                new_trips.extend(extract_trips(
                    chunk, chunk_closed, subject_id=subject_id,
                    person_id=person_ids[-1], circle_id=circle_id,
                    min_trip_fixes=self._cfg.min_trip_fixes,
                    open_stay_started_ts=open_started,
                ))
            # An open stay closes THIS cycle only once settled_seconds has
            # elapsed since its last fix (spec §3.2/§4) — a wall-clock
            # decision run_once makes, not segment_stays itself.
            if last_open_stay is not None:
                elapsed_s = (now_ms - last_open_stay.ended_ts) / 1000
                if (
                    elapsed_s >= self._cfg.settled_seconds
                    and last_open_stay.ended_ts - last_open_stay.started_ts
                    >= min_dwell_ms
                ):
                    new_stays.append(last_open_stay)
            if new_stays:
                await self._store.write_stays(new_stays)
                for closed_stay in new_stays:
                    try:
                        await self._record_rhythm(
                            subject_id=subject_id, stay=closed_stay, now_ms=now_ms,
                        )
                    except Exception:
                        # A Tier 3 derivation failure must NEVER block the Tier
                        # 1/2 watermark contract. `write_stays` has already
                        # durably committed these stays; if the exception
                        # escaped, `advance_fix_watermark` below would be
                        # skipped, the next tick would re-read the identical
                        # fixes, re-segment the identical stays and call
                        # `write_stays` again — and `presence_stays` has no
                        # unique constraint on (subject_id, started_ts), so
                        # that duplicates rows forever at tick cadence,
                        # poisoning `label_place`'s time-weighted shares and
                        # `cluster_places`' centroids. The realistic trigger is
                        # the analytics image deploying ahead of migration 0017
                        # (every occupancy/rhythm call raises UndefinedTable).
                        # Rhythm is derived and recoverable; the stay is not.
                        # Found by the final whole-branch review (Critical #1).
                        log.exception(
                            "rhythm recording failed for a closed stay — the "
                            "stay is already written and the watermark still "
                            "advances; rhythm is derived and recoverable, the "
                            "stay is not"
                        )
            if new_trips:
                # Tier 5's half of Tier 3's Critical #1 (commit 9616af3):
                # a derivation failure must NEVER strand the watermark, or
                # the next tick re-reads the identical fixes, re-segments
                # the identical stays and calls `write_stays` again against
                # a table with no unique constraint on
                # (subject_id, started_ts).
                #
                # The asymmetry with Tier 3 is REAL and accepted, and it is
                # sharper than "the watermark advances past them": these
                # trips are unrecoverable because `presence_trips` has no
                # `corridor` column at all (`SqlMobilityStore
                # .trips_for_subject` returns `corridor=()` on purpose), so
                # even the fixes' own address sequences are gone with them
                # — nothing downstream could reconstruct the input to retry.
                # Tier 3's stays, by contrast, are already committed before
                # its own guard is reached. Losing a batch of trips costs
                # this cycle's corridor samples; stranding the watermark
                # corrupts Tier 1/2 permanently, so this is the cheaper
                # failure by a wide margin — but it IS a loss, not a defer.
                try:
                    await self._store.write_trips(new_trips)
                except Exception:
                    log.exception(
                        "trip write failed — the watermark still advances, "
                        "so this batch's trips are a genuine accepted LOSS "
                        "(presence_trips carries no corridor column, so "
                        "nothing can reconstruct them), unlike Tier 3's "
                        "already-committed stays"
                    )
                else:
                    if self._metrics is not None:
                        self._metrics.inc(
                            "nora_analytics_trips_total", value=len(new_trips),
                            subject=subject_id,
                        )
                    try:
                        await self._record_corridors(
                            subject_id=subject_id, new_trips=new_trips, now_ms=now_ms,
                        )
                    except Exception:
                        # Same contract, one layer down: the trips are
                        # written, only their corridor linkage failed. That
                        # linkage is NOT recoverable next cycle either (the
                        # trips will not be in `new_trips` again and their
                        # sequences are not persisted), but it is still
                        # strictly cheaper than a stranded watermark. The
                        # realistic trigger is the analytics image deploying
                        # ahead of migration 0019 — every corridor write
                        # raises UndefinedTable.
                        log.exception(
                            "corridor derivation failed — the trips are "
                            "written and the watermark still advances; "
                            "these trips keep corridor_id NULL permanently"
                        )
            await self._store.advance_fix_watermark(
                subject_id=subject_id, fix_id=fixes[-1].id
            )
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_analytics_stays_total", value=len(new_stays), subject=subject_id,
                )

        # Speed 2 (cheap): the label recheck. Runs on EVERY call this
        # function is given, independent of whether clustering ran this
        # cycle — this is what lets max_age_seconds (the caller's fire
        # condition) actually do something at lag == 0 (spec §4.2).
        all_stays = await self._store.stays_for_subject(subject_id=subject_id)
        if all_stays:
            for existing_kind in ("home", "work"):
                existing = await self._store.existing_place(
                    subject_id=subject_id, kind=existing_kind
                )
                if existing is None:
                    continue
                _, place_lat, place_lng, _place_radius_m, _stored, _label = existing
                cluster_stays = [
                    s for s in all_stays
                    if _within(s, place_lat, place_lng, self._cfg.cluster_eps_m)
                ]
                recheck = label_place(
                    cluster_stays, now_ms=now_ms, tz_name=self._cfg.tz_name,
                    min_nights=self._cfg.min_nights,
                    min_weekdays=self._cfg.min_weekdays,
                    label_freshness_days=self._cfg.label_freshness_days,
                )
                if recheck.kind != existing_kind:
                    # `kind=existing_kind`, not `recheck.kind` — this write
                    # is scoped to the ONE slot being rechecked (e.g. a
                    # `home` recheck must never delete an unrelated,
                    # still-valid `work` row just because it downgrades to
                    # `unlabeled`).
                    label = await self._label_for(
                        lat=all_stays[0].lat, lng=all_stays[0].lng,
                    )
                    await self._store.upsert_place(
                        subject_id=subject_id, circle_id=circle_id,
                        kind=existing_kind,
                        lat=all_stays[0].lat, lng=all_stays[0].lng,
                        radius_m=self._cfg.cluster_eps_m, result=recheck,
                        now_ms=now_ms, label=label,
                    )
                    if self._metrics is not None and recheck.kind == "unlabeled":
                        self._metrics.inc(
                            "nora_analytics_unlabeled_total", subject=subject_id,
                        )

        # Speed 3 (expensive, dirty-set gated): full re-clustering. Only
        # when segmentation closed a stay since last_cluster_id — clustering
        # is idempotent on unchanged input, so a clock-driven re-run is pure
        # waste (spec §4.2).
        if fixes:
            new_last_cluster = fixes[-1].id
            if new_last_cluster > last_cluster_id:
                clusters = cluster_places(
                    all_stays, eps_m=self._cfg.cluster_eps_m,
                    min_samples=self._cfg.cluster_min_samples,
                )
                for cluster in clusters:
                    total_dwell = sum(s.ended_ts - s.started_ts for s in cluster) or 1
                    lat = sum(
                        s.lat * (s.ended_ts - s.started_ts) for s in cluster
                    ) / total_dwell
                    lng = sum(
                        s.lng * (s.ended_ts - s.started_ts) for s in cluster
                    ) / total_dwell
                    result = label_place(
                        cluster, now_ms=now_ms, tz_name=self._cfg.tz_name,
                        min_nights=self._cfg.min_nights,
                        min_weekdays=self._cfg.min_weekdays,
                        label_freshness_days=self._cfg.label_freshness_days,
                    )
                    if result.kind != "unlabeled":
                        # kind=result.kind: a freshly-clustered place writes
                        # into its OWN kind's slot, scoped the same way the
                        # recheck above is.
                        existing_before = await self._store.existing_place(
                            subject_id=subject_id, kind=result.kind
                        )
                        label = await self._label_for(lat=lat, lng=lng)
                        await self._store.upsert_place(
                            subject_id=subject_id, circle_id=circle_id,
                            kind=result.kind,
                            lat=lat, lng=lng, radius_m=self._cfg.cluster_eps_m,
                            result=result, now_ms=now_ms, label=label,
                        )
                        if self._metrics is not None:
                            self._metrics.inc(
                                "nora_analytics_places_total",
                                subject=subject_id, kind=result.kind,
                            )
                        if existing_before is None:
                            # First time THIS kind has ever existed for this
                            # subject — backfill past stays that predate it
                            # (Tier 3, design/27 §4): they closed before
                            # there was a place to attach rhythm/occupancy
                            # to, so they never went through `_record_rhythm`.
                            refreshed = await self._store.existing_place(
                                subject_id=subject_id, kind=result.kind
                            )
                            if refreshed is not None:
                                new_place_id = refreshed[0]
                                unlinked = [
                                    s for s in all_stays
                                    if s.place_id is None
                                    and _within(s, lat, lng, self._cfg.cluster_eps_m)
                                ]
                                for s in unlinked:
                                    await self._apply_rhythm(
                                        subject_id=subject_id, place_id=new_place_id,
                                        stay=s, now_ms=now_ms,
                                    )
                await self._store.advance_cluster_watermark(
                    subject_id=subject_id, fix_id=new_last_cluster
                )

    async def _record_corridors(
        self, *, subject_id: str, new_trips: list[Trip], now_ms: int,
    ) -> None:
        """Tier 5 (design/27 §4, issue #6): group THIS cycle's new trips
        into corridors, give each a durable identity, and recompute the
        touched corridors' hour-of-week speed percentiles.

        **Speed-3-ADJACENT, and gated on `new_trips` alone — no persisted
        `last_trip_cluster_id` watermark.** Not because a third watermark
        column would be expensive, but because it would have nothing to
        gate: this method CANNOT re-cluster a subject's full history even
        if it wanted to. `presence_trips` has no `corridor` column
        (Task 5's own decision), so `trips_for_subject` returns
        `corridor=()` for every stored row and `cluster_trips` over them
        would be meaningless. The full-history re-cluster the watermark
        would suppress does not exist as a code path.

        The arithmetic says the same thing independently, and it is worth
        recording because it is NOT the answer `cluster_places`' own
        precedent would predict. Measured on this machine (n trips with
        6 recurring corridors of 6-10 road names, jittered):

            cluster_trips  n=66     37 ms      n=1000  6.8 s
            cluster_trips  n=200   357 ms      n=3600   92 s
            cluster_places n=3600  6.0 s

        `cluster_places`' docstring calls its own O(n^2) "trivial" at 3,600
        stays and it is (6 s, gated). `cluster_trips` at the SAME n is
        **15x worse — 92 seconds of synchronous CPU inside one asyncio
        tick**, because an edit distance over two 6-10 element sequences is
        hundreds of Python-level operations where a haversine is a handful.
        So DEC-115's "94% of the expensive half was waste" reasoning does
        not merely carry over here, it is understated: a per-cycle full
        re-cluster at 720-day retention would be unaffordable, not just
        wasteful. What makes it moot is that `new_trips` is bounded by ONE
        batch — ~66 trips at `backfill_batch_limit`'s ~11 days of fixes
        (37 ms), and a handful in steady state. Even a 15x-pessimistic
        batch (n=1000, 6.8 s) fits inside `tick_seconds`.

        Cross-cycle continuity therefore comes from `existing_corridor`'s
        similarity match against the PERSISTED sequences, not from
        re-clustering — which is exactly why that method exists.
        """
        # Trips whose fixes carried no address at all are held out of BOTH
        # passes, and held out BEFORE clustering rather than filtered after
        # it. `edit_distance((), x) == len(x)` makes an empty corridor a
        # similarity-neighbour of every 1-2 element real corridor, so
        # `cluster_trips` will legitimately put an address-less trip in the
        # same cluster as three real, named ones — it is not only clusterable
        # with other address-less trips. Judging that cluster by its FIRST
        # trip's sequence is then a coin-flip on input order, wrong in both
        # directions: an empty `cluster[0]` discards the whole cluster and
        # silently unlinks the real trips in it, and a named `cluster[0]`
        # attaches the empty one too, contaminating the corridor's
        # `sample_count` and its speed percentiles with an ungrounded sample.
        # Partitioning first makes the question unaskable.
        # (Task 11 review, Important #1.)
        named_trips = [t for t in new_trips if t.corridor]
        clusters = cluster_trips(
            named_trips, max_edit_distance=self._cfg.corridor_max_edit_distance,
            min_samples=self._cfg.corridor_min_samples,
        )
        # id -> the corridor's CANONICAL sequence (the PERSISTED one for a
        # match, the representative for a fresh insert), so the reconciling
        # upsert below is handed the corridor's own identity rather than
        # whatever sequence happened to query it. Both stores currently
        # IGNORE `sequence` on the `existing_id` path by design
        # (`upsert_corridor`: "identity survives, does not chase every new
        # trip's own sequence"), so a mutation check confirms this choice is
        # not yet observable through the store's public surface — it is
        # carried because it is the correct value to pass, not because a
        # test can currently tell the difference.
        touched: dict[int, tuple[str, ...]] = {}
        clustered_starts = {t.started_ts for cluster in clusters for t in cluster}
        for cluster in clusters:
            # The cluster's FIRST trip's own sequence is the
            # representative — `PRESENCE_CORRIDORS`' own "identity
            # survives, does not chase every new trip's own sequence".
            attached = await self._attach_corridor(
                subject_id=subject_id, sequence=cluster[0].corridor,
                trips=cluster, now_ms=now_ms, may_create=True,
            )
            if attached is not None:
                touched[attached[0]] = attached[1]
        # A trip `cluster_trips` called noise can still belong to a corridor
        # this subject ALREADY has — and it usually does. `corridor_min_samples`
        # similar trips have to arrive in ONE batch to mint a corridor, which
        # happens on the initial backfill; after that a subject drives their
        # commute once or twice per cycle and every one of those trips is a
        # singleton. Without this second pass a corridor's `sample_count` and
        # its speed percentiles would freeze at whatever the backfill saw, and
        # Tier 5 would look built while silently never learning again.
        # Creating from a singleton is still refused — that is
        # `corridor_min_samples`' whole job.
        # `named_trips`, not `new_trips` — an address-less trip must not join
        # an existing corridor either, for the same edit-distance reason the
        # partition above exists: it would match any 1-2 element corridor
        # outright, which is a guess, and R-5 prefers silence to a wrong
        # answer. It therefore ends this method with `corridor_id` still NULL,
        # which is the honest record of "no address, so no route".
        for trip in named_trips:
            if trip.started_ts in clustered_starts:
                continue
            attached = await self._attach_corridor(
                subject_id=subject_id, sequence=trip.corridor,
                trips=[trip], now_ms=now_ms, may_create=False,
            )
            if attached is not None:
                touched[attached[0]] = attached[1]
        for corridor_id, sequence in sorted(touched.items()):
            await self._record_corridor_speeds(
                subject_id=subject_id, corridor_id=corridor_id,
                sequence=sequence, now_ms=now_ms,
            )

    async def _attach_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], trips: list[Trip],
        now_ms: int, may_create: bool,
    ) -> tuple[int, tuple[str, ...]] | None:
        """Link `trips` to the corridor `sequence` names, creating it when
        `may_create` and no near-match exists. Returns `(corridor_id,
        canonical_sequence)`, or `None` when nothing was attached."""
        match = await self._store.existing_corridor(
            subject_id=subject_id, sequence=sequence,
            max_edit_distance=self._cfg.corridor_max_edit_distance,
        )
        if match is None and not may_create:
            return None
        existing_id, canonical = (None, sequence) if match is None else match
        # `sample_count=len(trips)` is PROVISIONAL for an existing corridor —
        # `_record_corridor_speeds` overwrites it with the authoritative count
        # once the links have landed. It cannot be authoritative here: the id
        # is needed to link, and the count is only knowable after linking.
        # For a fresh insert it is already exact (no other trip carries the
        # id yet), so the reconcile is a no-op in that case.
        corridor_id = await self._store.upsert_corridor(
            subject_id=subject_id, sequence=canonical, sample_count=len(trips),
            now_ms=now_ms, existing_id=existing_id,
        )
        await self._store.set_trip_corridor_ids(
            subject_id=subject_id,
            started_ts_in=[t.started_ts for t in trips],
            corridor_id=corridor_id,
        )
        if self._metrics is not None:
            self._metrics.inc("nora_analytics_corridors_total", subject=subject_id)
        return corridor_id, canonical

    async def _record_corridor_speeds(
        self, *, subject_id: str, corridor_id: int, sequence: tuple[str, ...],
        now_ms: int,
    ) -> None:
        """Per-hour-of-week p50/p90 over EVERY trip ever linked to this
        corridor — a full recompute from the whole sample set, never an
        incremental update, the same posture `_apply_rhythm` takes."""
        samples = await self._store.trip_speeds_for_corridor(
            subject_id=subject_id, corridor_id=corridor_id,
        )
        if not samples:
            return
        # The authoritative sample count, now that the links have landed —
        # see `_attach_corridor`'s provisional write.
        await self._store.upsert_corridor(
            subject_id=subject_id, sequence=sequence, sample_count=len(samples),
            now_ms=now_ms, existing_id=corridor_id,
        )
        buckets: dict[int, list[float]] = {}
        for started_ts, speed_kmh in samples:
            buckets.setdefault(
                hour_of_week(started_ts, self._cfg.tz_name), []
            ).append(speed_kmh)
        for how, speeds in buckets.items():
            ordered = sorted(speeds)
            await self._store.upsert_corridor_speed(
                subject_id=subject_id, corridor_id=corridor_id, hour_of_week=how,
                p50_kmh=_percentile(ordered, 0.5), p90_kmh=_percentile(ordered, 0.9),
                sample_count=len(ordered), now_ms=now_ms,
            )

    async def _record_rhythm(self, *, subject_id: str, stay: Stay, now_ms: int) -> None:
        """Tier 3 (design/27 §4, issue #6): occupancy + rhythm + anomaly
        for ONE closed stay, only when it falls inside an existing
        home/work place — an unlabelled cluster is never persisted (Tier
        2's own rule), so rhythm has nothing to attach to until then."""
        place_id: int | None = None
        for kind in ("home", "work"):
            existing = await self._store.existing_place(subject_id=subject_id, kind=kind)
            if existing is None:
                continue
            pid, place_lat, place_lng, _radius, _result, _label = existing
            if _within(stay, place_lat, place_lng, self._cfg.cluster_eps_m):
                place_id = pid
                break
        if place_id is None:
            return
        await self._apply_rhythm(
            subject_id=subject_id, place_id=place_id, stay=stay, now_ms=now_ms,
        )

    async def _apply_rhythm(
        self, *, subject_id: str, place_id: int, stay: Stay, now_ms: int,
    ) -> None:
        all_stays = await self._store.stays_for_subject(subject_id=subject_id)
        current = next(
            (s for s in all_stays if s.started_ts == stay.started_ts), None,
        )
        if current is not None and current.place_id is not None and current.place_id != place_id:
            # Already linked to a DIFFERENT place — a same-cycle race between
            # two first-place backfills reading the same stale snapshot, or a
            # stay `_record_rhythm` already handled this cycle. set_place_ids
            # would no-op; attributing this stay's dwell to `place_id` anyway
            # would double-count it across two places' occupancy/rhythm.
            # Found by Task 10's review, Critical #2.
            return

        already_linked = current is not None and current.place_id == place_id
        if not already_linked:
            # The ONLY branch that may record occupancy or score an anomaly:
            # this is the first time the stay is being attributed to
            # `place_id`. `record_occupancy` ACCUMULATES dwell
            # (`dwell_ms = dwell_ms + excluded.dwell_ms`) — correct for many
            # distinct stays, catastrophic if the SAME stay is replayed, and
            # nothing else guards that. A re-fired first-place backfill, or a
            # caller retry after the `_record_rhythm` guard in `run_once`
            # swallowed an exception, both re-enter here for an
            # already-linked stay; without this gate each replay would re-add
            # the stay's dwell to `presence_occupancy` and re-score it for
            # anomaly. Found by the final whole-branch review (Important #2).
            duration_ms = stay.ended_ts - stay.started_ts
            # Baseline read BEFORE this stay is linked, so it is never
            # anomalous relative to itself.
            history = await self._store.stay_durations_for_place(
                subject_id=subject_id, place_id=place_id,
            )
            z = robust_z_score(
                float(duration_ms), [float(d) for d in history],
                min_samples=self._cfg.anomaly_min_samples,
            )
            if (
                z is not None
                and abs(z) >= self._cfg.anomaly_z_threshold
                and self._metrics is not None
            ):
                self._metrics.inc("nora_analytics_anomalies_total", subject=subject_id)

            await self._store.set_place_ids(
                subject_id=subject_id, started_ts_in=[stay.started_ts],
                place_id=place_id,
            )
            bins = hour_of_week_bins(stay.started_ts, stay.ended_ts, self._cfg.tz_name)
            await self._store.record_occupancy(
                subject_id=subject_id, place_id=place_id, bins=bins, now_ms=now_ms,
            )
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_analytics_occupancy_bins_total",
                    value=len(bins), subject=subject_id,
                )

        # Rhythm recomputes from EVERY stay now linked to this place — re-read
        # fresh (set_place_ids may have just landed). Unlike occupancy this is
        # a FULL recompute, not an accumulation, so it is idempotent and runs
        # unconditionally — including as a harmless redundant call for an
        # already-linked stay. `linked` is guaranteed non-empty: either
        # `current` already belonged to `place_id` (the early-return above
        # would have caught any mismatch), or set_place_ids just linked it, so
        # the re-read below will show it.
        all_stays = await self._store.stays_for_subject(subject_id=subject_id)
        linked = [s for s in all_stays if s.place_id == place_id]
        tz = ZoneInfo(self._cfg.tz_name)
        arrivals = [_local_minute_of_day(s.started_ts, tz) for s in linked]
        departures = [_local_minute_of_day(s.ended_ts, tz) for s in linked]
        durations = [float(s.ended_ts - s.started_ts) for s in linked]
        sorted_d = sorted(durations)
        n = len(sorted_d)
        median_ms = int(
            sorted_d[n // 2] if n % 2 else (sorted_d[n // 2 - 1] + sorted_d[n // 2]) / 2
        )
        sorted_dev = sorted(abs(d - median_ms) for d in durations)
        mad_ms = int(
            sorted_dev[n // 2] if n % 2
            else (sorted_dev[n // 2 - 1] + sorted_dev[n // 2]) / 2
        )
        await self._store.upsert_rhythm(
            subject_id=subject_id, place_id=place_id,
            mean_arrival_minute=circular_mean_minute(arrivals),
            mean_departure_minute=circular_mean_minute(departures),
            duration_median_ms=median_ms, duration_mad_ms=mad_ms,
            sample_count=len(linked), now_ms=now_ms,
        )


def _percentile(ordered: list[float], q: float) -> float:
    """Nearest-rank percentile over an ALREADY-SORTED, non-empty list —
    `ordered[int(len * q)]`, clamped to the last index.

    Deliberately NOT `statistics.median` / `quantiles`: those interpolate
    between neighbours, inventing a speed no trip was ever measured at,
    which this substrate's "bind to the real property" rule rules out. The
    value returned is always one an actual trip actually recorded.

    For ODD `len` the q=0.5 case coincides exactly with
    `statistics.median` (verified in
    `test_corridor_speed_percentiles_match_a_hand_computation`); for even
    `len` it is the upper of the two middle samples rather than their
    average, for the same never-interpolate reason.

    The `min(...)` never actually fires at this module's two call sites:
    `int(n * q) <= n - 1` for every `n >= 1` when `q <= 0.9`. It is there
    for a `q` that does not exist yet (1.0 runs one past the end), and the
    test asserts it at exactly that value rather than pretending 0.9
    exercises it."""
    return ordered[min(int(len(ordered) * q), len(ordered) - 1)]


def _local_minute_of_day(ts_ms: int, tz: ZoneInfo) -> float:
    local = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
    return local.hour * 60.0 + local.minute + local.second / 60.0


def _within(stay: Stay, place_lat: float, place_lng: float, eps_m: float) -> bool:
    return haversine_distance_m(stay.lat, stay.lng, place_lat, place_lng) <= eps_m


def _chunk_by_gap(fixes: list[RawFix], gap_ms: int) -> list[list[RawFix]]:
    """Split ts-ascending `fixes` wherever the gap to the next fix exceeds
    `gap_ms` — see the long comment at this function's one call site for
    why `run_once` needs this before handing a batch to `segment_stays`."""
    if not fixes:
        return []
    chunks: list[list[RawFix]] = [[fixes[0]]]
    for prev, cur in pairwise(fixes):
        if cur.ts - prev.ts > gap_ms:
            chunks.append([])
        chunks[-1].append(cur)
    return chunks
