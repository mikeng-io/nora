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

from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Protocol

from nora.domain.mobility import (
    DWELL_CAP_MS,
    LabelResult,
    RawFix,
    Stay,
    cluster_places,
    label_place,
    segment_stays,
)
from nora.domain.presence import haversine_distance_m


class MobilityStore(Protocol):
    async def watermark(self, *, subject_id: str) -> tuple[int, int]: ...
    async def fixes_since(
        self, *, person_ids: list[str], after_fix_id: int, grace_ms: int,
        now_ms: int, limit: int,
    ) -> list[RawFix]: ...
    async def write_stays(self, stays: list[Stay]) -> None: ...
    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]: ...
    async def existing_place(
        self, *, subject_id: str, kind: str
    ) -> tuple[int, float, float, float, LabelResult] | None: ...
    async def upsert_place(
        self, *, subject_id: str, circle_id: str, kind: str, lat: float,
        lng: float, radius_m: float, result: LabelResult, now_ms: int,
    ) -> None: ...
    async def advance_fix_watermark(self, *, subject_id: str, fix_id: int) -> None: ...
    async def advance_cluster_watermark(self, *, subject_id: str, fix_id: int) -> None: ...


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


class MobilityRollup:
    def __init__(
        self, *, store: MobilityStore, config: RollupConfig, metrics: Any = None,
    ) -> None:
        self._store = store
        self._cfg = config
        self._metrics = metrics

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
            raw = [RawFix(id=f.id, ts=f.ts, lat=f.lat, lng=f.lng) for f in fixes]
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
                if seg.open_stay is None:
                    continue
                if i == len(chunks) - 1:
                    # Only the FINAL chunk's trailing group is genuinely
                    # open — nothing has been observed yet to say the
                    # subject left; that is `settled_seconds`' job below.
                    last_open_stay = seg.open_stay
                elif seg.open_stay.ended_ts - seg.open_stay.started_ts >= min_dwell_ms:
                    # A non-final chunk's trailing group ended because the
                    # NEXT chunk's first fix was far enough away in time —
                    # an observed gap, not a guess — so it closes now,
                    # subject to the same min-dwell gate `segment_stays`'
                    # own `_close` applies.
                    new_stays.append(seg.open_stay)
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
                _, place_lat, place_lng, _place_radius_m, _stored = existing
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
                    await self._store.upsert_place(
                        subject_id=subject_id, circle_id=circle_id,
                        kind=existing_kind,
                        lat=all_stays[0].lat, lng=all_stays[0].lng,
                        radius_m=self._cfg.cluster_eps_m, result=recheck,
                        now_ms=now_ms,
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
                        await self._store.upsert_place(
                            subject_id=subject_id, circle_id=circle_id,
                            kind=result.kind,
                            lat=lat, lng=lng, radius_m=self._cfg.cluster_eps_m,
                            result=result, now_ms=now_ms,
                        )
                        if self._metrics is not None:
                            self._metrics.inc(
                                "nora_analytics_places_total",
                                subject=subject_id, kind=result.kind,
                            )
                await self._store.advance_cluster_watermark(
                    subject_id=subject_id, fix_id=new_last_cluster
                )


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
