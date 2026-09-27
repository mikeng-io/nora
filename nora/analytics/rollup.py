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

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from nora.domain.mobility import (
    CHUNK_GAP_MS,
    DWELL_CAP_MS,
    DerivedPlace,
    PlannedPlace,
    RawFix,
    Stay,
    TaggedPlace,
    Trip,
    circular_mean_minute,
    cluster_trips,
    extract_trips,
    hour_of_week,
    hour_of_week_bins,
    label_place,
    mode_address,
    plan_places,
    segment_stays,
    trailing_fix_is_settled,
)
from nora.domain.mobility_forecast import detect_stay_anomalies
from nora.domain.ports import GeocodeCacheStore

log = logging.getLogger("nora.analytics.rollup")


class MobilityStore(Protocol):
    async def watermark(self, *, subject_id: str) -> tuple[int, int]: ...
    async def fixes_since(
        self, *, person_ids: list[str], after_fix_id: int, grace_ms: int,
        now_ms: int, limit: int,
    ) -> list[RawFix]: ...
    async def write_stays(self, stays: list[Stay]) -> None: ...
    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]: ...
    async def write_trips(self, trips: list[Trip]) -> None: ...
    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]: ...
    async def unclustered_named_trips_for_subject(
        self, *, subject_id: str, limit: int
    ) -> list[Trip]: ...
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
    async def places_for_subject(self, *, subject_id: str) -> list[DerivedPlace]: ...
    async def save_place(
        self, *, subject_id: str, circle_id: str, place_id: int | None,
        lat: float, lng: float, radius_m: float,
        kind: str, confidence: float, label: str | None,
        saved_place_id: str | None, first_seen_ts: int, last_seen_ts: int,
    ) -> int: ...
    async def delete_places(self, *, subject_id: str, place_ids: list[int]) -> None: ...
    async def relink_stays(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None: ...
    async def tagged_places(self) -> list[TaggedPlace]: ...
    async def advance_fix_watermark(self, *, subject_id: str, fix_id: int) -> None: ...
    async def advance_cluster_watermark(self, *, subject_id: str, fix_id: int) -> None: ...
    async def replace_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
    ) -> None: ...
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
    corridor_cluster_batch_limit: int = 500
    min_trip_fixes: int = 2
    max_trip_duration_seconds: float = 10800.0
    # DEC-190's match radius (design/27 §3.7): a tagged place claims a stay
    # or a cluster this close. Its OWN constant, never `cluster_eps_m` —
    # measured match band 7.0-49.3 m against a nearest non-match at 636 m, so
    # safe anywhere in 100-500 m and unsafe at 50.
    place_match_radius_m: float = 150.0


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

        # Declared before the `if fixes:` block so Speed 3's dirty-set gate
        # below can reference it without a possibly-unbound warning; when
        # `fixes` is empty both stay and trip lists are empty by definition.
        new_stays: list[Stay] = []
        new_trips: list[Trip] = []

        if fixes:
            # `address=` is load-bearing from Tier 5 on (Task 2): it is the
            # ONLY input `extract_trips` has for a trip's corridor
            # sequence, and dropping it here silently produced empty
            # corridors that cluster with each other and nothing real.
            # `is_driving=` is load-bearing the same way (2026-08-20
            # trip-settlement design §2.2): it is the PRIMARY settlement
            # signal, and this projection is the only place between the
            # store and `extract_trips` that can drop it. It was dropped
            # here when the field was added (Task 1 threaded it through both
            # stores, and every fix arrived at segmentation as
            # `is_driving=None` anyway), which silently reduced the whole
            # settlement mechanism below to its `settled_seconds` fallback —
            # a wiring gap that no test in `tests/unit/test_mobility.py`
            # could see, because those call `extract_trips` directly.
            # THE BATCH-STALL FIX (audit 2026-09-27). A hold (an undecided
            # trip, or an open stay) that starts at the FIRST fix of a batch
            # leaves the watermark exactly where it was — and when the batch
            # was cut at `batch_limit`, the next tick reads the SAME fixes and
            # decides the same way, forever. 400 fixes at the 5 s moving
            # cadence is a 33-minute drive: any longer drive froze the
            # subject's derivation until `backfill_threshold` (4,000) fixes of
            # backlog had piled up, which at resting cadence is a day or
            # more, and every stay and trip after the drive — an evening out,
            # dinner, the way home — was simply not derived in the meantime.
            # Reproduced tick by tick in `tests/unit/test_analytics_rollup.py::
            # test_a_drive_longer_than_one_batch_does_not_freeze_the_evening`.
            # The fix widens THIS cycle's batch (the probe already read up to
            # `backfill_batch_limit` fixes) until the hold resolves or the
            # probe is exhausted; nothing is written until the batch is final.
            limit = len(fixes)
            max_limit = min(len(lag_probe), self._cfg.backfill_batch_limit)
            while True:
                fixes = lag_probe[:limit]
                new_stays, new_trips, trip_hold_id, stay_hold_id = self._segment_batch(
                    fixes, subject_id=subject_id, person_ids=person_ids,
                    circle_id=circle_id, now_ms=now_ms,
                )
                watermark_target = fixes[-1].id
                if trip_hold_id is not None:
                    watermark_target = min(watermark_target, trip_hold_id - 1)
                if stay_hold_id is not None:
                    watermark_target = min(watermark_target, stay_hold_id - 1)
                if watermark_target > last_fix_id or limit >= max_limit:
                    break
                limit = min(max_limit, limit * 2)
            if new_stays:
                # Place linking, occupancy, rhythm and anomaly scoring for
                # these stays happen in Speed 3's reconcile below, which the
                # `or new_stays` gate guarantees runs this cycle.
                await self._store.write_stays(new_stays)
            if new_trips:
                # Tier 5's half of Tier 3's Critical #1 (commit 9616af3):
                # a derivation failure must NEVER strand the watermark, or
                # the next tick re-reads the identical fixes, re-segments
                # the identical stays and calls `write_stays` again against
                # a table with no unique constraint on
                # (subject_id, started_ts).
                #
                # The asymmetry with Tier 3 is REAL and accepted: these
                # trips are unrecoverable, because the fixes that produced
                # them are behind the watermark and nothing re-derives
                # them. Tier 3's stays, by contrast, are already committed
                # before its own guard is reached. Losing a batch of trips
                # costs those trips' corridor samples; stranding the
                # watermark corrupts Tier 1/2 permanently, so this is the
                # cheaper failure by a wide margin — but it IS a loss, not
                # a defer.
                try:
                    await self._store.write_trips(new_trips)
                except Exception:
                    log.exception(
                        "trip write failed — the watermark still advances, "
                        "so this batch's trips are a genuine accepted LOSS "
                        "(the fixes behind them are consumed and nothing "
                        "re-derives them), unlike Tier 3's already-committed "
                        "stays"
                    )
                else:
                    if self._metrics is not None:
                        self._metrics.inc(
                            "nora_analytics_trips_total", value=len(new_trips),
                            subject=subject_id,
                        )
                    try:
                        await self._record_corridors(
                            subject_id=subject_id, now_ms=now_ms,
                        )
                    except Exception:
                        # Same contract, one layer down — but unlike the
                        # write above, this one IS recoverable now. The
                        # trips are committed WITH their `sequence`, and
                        # their `corridor_id` is still NULL, which is
                        # exactly the predicate
                        # `unclustered_named_trips_for_subject` selects on:
                        # the next cycle that writes a trip re-reads these
                        # and clusters them again. Migration 0020 is what
                        # turned "permanently unlinked" into "retried next
                        # cycle". The realistic trigger is the analytics
                        # image deploying ahead of migration 0019/0020 —
                        # every corridor write raises UndefinedTable, and
                        # every cycle after the migration lands recovers.
                        log.exception(
                            "corridor derivation failed — the trips are "
                            "written with their sequences and the watermark "
                            "still advances; they keep corridor_id NULL and "
                            "are re-clustered on a later cycle"
                        )
            # The watermark stops one short of anything `extract_trips`
            # refused to finalize, so the undecided group is re-read WHOLE
            # next cycle rather than consumed as a fragment. This is the
            # half of the settlement fix that makes the other half safe:
            # holding a group back is only not a data loss if its fixes stay
            # readable.
            #
            # A TRIP hold reduces this below `fixes[-1].id` when the
            # trailing group is undecided movement. A STAY hold (issue #80)
            # does the same when the trailing group is an open stay that
            # has not yet settled — its fixes must stay readable until the
            # radius break or the clock closes it, or the stay is silently
            # dropped (the production mechanism that halted stays for 10
            # days). Both holds are bounded: the stay hold releases when
            # `settled_seconds` elapses (the block above), the trip hold
            # when the drive settles or `max_trip_duration_seconds` fires.
            #
            # Never moves backwards: every held-back fix came from `fixes`,
            # which `fixes_since` already filtered to `id > last_fix_id`, so
            # `trip_hold_id - 1 >= last_fix_id`. The degenerate case — the
            # WHOLE batch is one undecided group, so the target equals the
            # current watermark — is a no-op write, not a special case.
            await self._store.advance_fix_watermark(
                subject_id=subject_id, fix_id=watermark_target
            )
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_analytics_stays_total", value=len(new_stays), subject=subject_id,
                )

        # Speed 2 (cheap): the label recheck. Runs on EVERY call this
        # function is given, independent of whether clustering ran this
        # cycle — this is what lets max_age_seconds (the caller's fire
        # condition) actually do something at lag == 0 (spec §4.2). Each
        # stored place is relabelled from the stays LINKED to it — never from
        # a radius test around its stored point, and never written back at
        # any coordinate but its own (the old recheck wrote the subject's
        # chronologically first stay's lat/lng onto a flipped row, a second
        # path to a place kilometres from its stays; issue #339).
        all_stays = await self._store.stays_for_subject(subject_id=subject_id)
        for place in await self._store.places_for_subject(subject_id=subject_id):
            linked = [s for s in all_stays if s.place_id == place.id]
            if not linked:
                continue
            recheck = label_place(
                linked, now_ms=now_ms, tz_name=self._cfg.tz_name,
                min_nights=self._cfg.min_nights,
                min_weekdays=self._cfg.min_weekdays,
                label_freshness_days=self._cfg.label_freshness_days,
            )
            if recheck.kind == place.kind and recheck.confidence == place.confidence:
                continue
            await self._store.save_place(
                subject_id=subject_id, circle_id=place.circle_id,
                place_id=place.id, lat=place.lat, lng=place.lng,
                radius_m=place.radius_m, kind=recheck.kind,
                confidence=recheck.confidence, label=place.label,
                saved_place_id=place.saved_place_id,
                first_seen_ts=min(s.started_ts for s in linked),
                last_seen_ts=max(s.ended_ts for s in linked),
            )
            if (
                self._metrics is not None
                and recheck.kind == "unlabeled" and place.kind != "unlabeled"
            ):
                self._metrics.inc("nora_analytics_unlabeled_total", subject=subject_id)

        # Speed 3 (expensive, dirty-set gated): the place reconcile. Only
        # when fixes or stays arrived since last_cluster_id — it is
        # idempotent on unchanged input, so a clock-driven re-run is pure
        # waste (spec §4.2).
        if fixes:
            new_last_cluster = fixes[-1].id
            if new_last_cluster > last_cluster_id or new_stays:
                # `or new_stays`: a stay closed purely by the clock (issue
                # #80 stay-hold cycle — the batch is the SAME held fixes the
                # previous cycle already clustered at, so the id gate alone
                # is false) still needs linking now, not on some unrelated
                # future fix.
                try:
                    await self._reconcile_places(
                        subject_id=subject_id, circle_id=circle_id,
                        all_stays=all_stays, new_stays=new_stays, now_ms=now_ms,
                    )
                except Exception:
                    # The fix watermark has already advanced above, so the
                    # stays are safe (Tier 3's Critical #1 contract). The
                    # CLUSTER watermark deliberately does not advance: the
                    # reconcile is idempotent and re-runs next cycle, which
                    # is what makes a place-side failure recoverable rather
                    # than a permanently unlinked batch.
                    log.exception(
                        "place reconcile failed — stays are written and the "
                        "fix watermark advanced; the reconcile retries next cycle"
                    )
                else:
                    await self._store.advance_cluster_watermark(
                        subject_id=subject_id, fix_id=new_last_cluster
                    )

    def _segment_batch(
        self, fixes: list[Any], *, subject_id: str, person_ids: list[str],
        circle_id: str, now_ms: int,
    ) -> tuple[list[Stay], list[Trip], int | None, int | None]:
        """Speed 1 over one batch: `(new_stays, new_trips, trip_hold_id,
        stay_hold_id)`. Pure with respect to the store — `run_once` may call
        it more than once on growing batches before it writes anything (the
        batch-stall fix below)."""
        new_stays: list[Stay] = []
        new_trips: list[Trip] = []
        raw = [
            RawFix(
                id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address,
                is_driving=f.is_driving,
            )
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
        # The lowest fix id `extract_trips` refused to finalize this
        # cycle — the watermark stops one short of it below so the
        # undecided group is re-read whole next cycle instead of being
        # consumed as a fragment.
        trip_hold_id: int | None = None
        chunks = _chunk_by_gap(raw, CHUNK_GAP_MS)
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
            is_final_chunk = i == len(chunks) - 1
            # THE trip-settlement fix (2026-08-20 design §2.2/§2.3).
            #
            # `segment_stays` ALWAYS returns a trailing `open_stay` for a
            # non-empty window — read its own final paragraph: whatever
            # remains in `group` is the open stay, unconditionally, even
            # when that "group" is one fix taken at 90 km/h. So the
            # branch below used to fire on EVERY final chunk, handing
            # `extract_trips` a cut point that, mid-drive, sits at the
            # newest fix. Every tick therefore flushed "everything except
            # the newest fix" as a finished trip, and a continuous drive
            # came out as one trip per tick — the live fragmentation the
            # design's §0 evidence is made of. Task 2's settlement check
            # is gated on `open_stay_started_ts is None`, so without this
            # guard it could never have fired from here at all.
            #
            # The discriminator is `trailing_fix_is_settled` — the SAME
            # predicate, on the SAME fix (`chunk[-1]` is `extract_trips`'
            # own `buffer[-1]` for the final chunk), that `extract_trips`
            # uses to decide whether to finalize. Sharing it is not
            # tidiness: if these two ever disagreed, the same fixes would
            # be held back as candidate TRIP fixes here and written as a
            # STAY below, landing in both tables.
            #
            # Unsettled means there is no stay-in-progress to cut at and
            # the trailing group's fate is genuinely undetermined — which
            # is precisely the state `open_stay_started_ts=None` means.
            # `last_open_stay` is deliberately left unset in that case
            # too, for the same reason: a group being held back must not
            # also be closable as a stay by the `settled_seconds` block
            # below (`test_a_gridlocked_drive_writes_one_stay_once`).
            #
            # All three readings matter, and `None` is NOT a synonym for
            # `False` here. `True` (still driving) never settles. `False`
            # is a real arrival — the parked tail IS a stay-in-progress,
            # and calling it movement would swallow every stay this
            # substrate exists to find
            # (`test_a_parked_tail_with_is_driving_false_is_still_an_
            # open_stay`). `None` is no reading at all — the real state
            # of every historical fix and anything Life360 omits the flag
            # on — and falls back to `settled_seconds`, the same
            # quiet-boundary clock Tier 1 already uses for a stay's
            # arrival (design §2, bullet 2). Treating `None` as settled
            # left the original fragmentation bug fully intact for
            # exactly the fixes that have no signal to fix it with.
            tail_settled = trailing_fix_is_settled(
                chunk[-1], now_ms=now_ms,
                settled_seconds=self._cfg.settled_seconds,
            )
            # `tail_dwelled` is the SECOND gate. The clock alone is not
            # enough: at a RESTING cadence (Life360 emits every ~10 min
            # when still) the trailing fix is never older than
            # `settled_seconds`, so a subject sitting at home all day
            # would have their whole day held back as "undecided
            # movement" — and when it finally flushed, their stationary
            # fixes came out as 0 km/h, 0 m trips instead of a stay.
            # Measured on
            # `test_a_repeating_commute_actually_grows_corridors_over_
            # days`: 41 such phantom trips, and 7 stays where the fixture
            # documents zero.
            #
            # The discriminator is the trailing group's own DWELL. A
            # resting tail is one spatially-coherent group whose span
            # passes `min_dwell_ms` — the same bar `_close` uses to call
            # something a stay. A driving tail is a fix that just broke
            # the radius, so its group's span is ~0. So: no signal, but
            # the tail has already dwelled like a stay -> treat it as the
            # stay-in-progress it looks like.
            #
            # Originally this gate applied ONLY to `is_driving is None`
            # — "when Life360 actually says `is_driving`, that reading
            # wins over the spatial proxy" (gridlock is a real drive
            # whose tail looks spatially like a stay). Production
            # evidence (issue #80) overturned that: Life360's flag is
            # NOISY AT REST — people sitting at home produce
            # `is_driving=True` fixes (66-100 such fixes per person at
            # their home coordinates, verified 2026-08-29). With the
            # flag winning over the spatial proxy, a genuine home stay
            # whose tail fix happens to read `is_driving=True` is never
            # settled, never closed, and its fixes are watermark-consumed
            # forever — stays generation halted for 10 days after the
            # 2026-08-20 trip-settlement fix wired the flag through.
            # The spatial proxy is now the discriminator for `True` too:
            # a tail that has already dwelled past `min_dwell_ms` is the
            # stay-in-progress it looks like, whatever the flag says.
            # The gridlock false-positive this admits (a real drive
            # stuck in traffic whose tail dwells past the bar) is the
            # accepted trade — it writes ONE stay, once, and the
            # watermark advances past it; the flag-noise false-negative
            # it fixes loses every stay a subject actually had.
            tail_dwelled = (
                seg.open_stay is not None
                and seg.open_stay.ended_ts - seg.open_stay.started_ts
                >= min_dwell_ms
            )
            tail_is_movement = (
                is_final_chunk and not tail_settled and not tail_dwelled
            )
            if seg.open_stay is not None and not tail_is_movement:
                if is_final_chunk:
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
            # Only the FINAL chunk gets the settlement args. A non-final
            # chunk's trailing group ended because an OBSERVED gap
            # followed it, so its fate is already decided and there is
            # nothing to settle; `now_ms=None` is what reproduces the
            # original always-flush behaviour for it (extract_trips'
            # own docstring).
            chunk_trips, held_back = extract_trips(
                chunk, chunk_closed, subject_id=subject_id,
                person_id=person_ids[-1], circle_id=circle_id,
                min_trip_fixes=self._cfg.min_trip_fixes,
                open_stay_started_ts=open_started,
                now_ms=now_ms if is_final_chunk else None,
                settled_seconds=(
                    self._cfg.settled_seconds if is_final_chunk else None
                ),
                max_trip_duration_seconds=(
                    self._cfg.max_trip_duration_seconds
                    if is_final_chunk else None
                ),
            )
            # An ARRIVAL RACE (audit 2026-09-27): the trip was cut at the start of a
            # trailing group that has not yet dwelled `min_dwell_ms` — a PROVISIONAL
            # stop. The next fix often jitters it apart, the stay then starts a few
            # fixes later, and the fixes between the already-written trip and the
            # real stay came out as a second, orphan "trip" of a few hundred metres
            # at walking pace — one phantom per arrival. While the stop is
            # unconfirmed (and still inside the resting-cadence clock), hold the trip
            # that runs into it as well; it is cut for good once the stop confirms,
            # or rejoins the movement if the stop dissolves.
            if (
                open_started is not None and chunk_trips
                and seg.open_stay is not None
                and seg.open_stay.ended_ts - seg.open_stay.started_ts < min_dwell_ms
                and (now_ms - chunk[-1].ts) / 1000 < DWELL_CAP_MS / 1000
                and not any(c.started_ts > chunk_trips[-1].ended_ts for c in chunk_closed)
            ):
                provisional = chunk_trips.pop()
                held_back = held_back or [
                    f for f in chunk if f.ts >= provisional.started_ts
                ]
            new_trips.extend(chunk_trips)
            if held_back:
                trip_hold_id = min(f.id for f in held_back)
        # An open stay closes THIS cycle only once settled_seconds has
        # elapsed since its last fix (spec §3.2/§4) — a wall-clock
        # decision run_once makes, not segment_stays itself.
        #
        # If it does NOT close this cycle, its fixes must stay readable:
        # the watermark advancing past them would mean the next cycle
        # re-segments only the NEW fixes, the open stay's true arrival
        # time is lost, and when the subject finally leaves, the radius
        # break that would close the home group never fires (the home
        # fixes are no longer in the batch) — the stay is silently
        # dropped. That is the production mechanism behind issue #80:
        # stays generation halted for 10 days because every overnight
        # home session was consumed fix-by-fix and never closed. This is
        # the stay-side counterpart of the trip hold below: hold the
        # watermark to the fix immediately before the open stay's first
        # fix, so the whole group is re-read next cycle until its fate
        # is decided (radius break closes it, or the clock settles it).
        stay_hold_id: int | None = None
        if last_open_stay is not None:
            elapsed_s = (now_ms - last_open_stay.ended_ts) / 1000
            span_ms = last_open_stay.ended_ts - last_open_stay.started_ts
            if (
                elapsed_s >= DWELL_CAP_MS / 1000
                and span_ms >= min_dwell_ms
            ):
                # The close clock is DWELL_CAP_MS (31.7 min), NOT
                # `settled_seconds` (15 min), and it is deliberately the
                # SAME clock the hold branch below uses. `settled_seconds`
                # here was the 2026-09-17 production defect: measured over
                # 26,047 inter-fix gaps across 14 live days, the p90 gap is
                # 30.1 min and the p99 is 30.2 — so 15 min of silence is
                # what a resting subject looks like BETWEEN two fixes of
                # one stay, not evidence the stay ended. Two fixes clear
                # `min_dwell_ms` (10 min), so this gate fired between every
                # pair, closed the group at 2 fixes, and the next fix
                # started a fresh one: 3,130 stays in `presence_stays`
                # where a clean re-derive of the same fixes at the same
                # parameters gives 1,399, median duration 30.1 min against
                # 65.2, and 42% of rows pinned in a 29-32.5 min band that
                # is the cadence rather than behaviour. It also fed Tier 3
                # a degenerate anomaly baseline — `presence_place_rhythm`
                # MADs of 500-5,000 ms against a ~1,804,000 ms median, so
                # |z| >= 3 tripped on a ~4-second deviation.
                #
                # The hold branch below already carried this reasoning
                # ("settled_seconds (15 min) is too short for this job")
                # when issue #82 raised the HOLD ceiling to DWELL_CAP_MS —
                # but the close gate above it was left on the old clock, so
                # it fired first and the hold was unreachable for any group
                # past min-dwell. One clock, read in both places, is the
                # fix. 31.7 min sits above the p99 gap with ~1.5 min of
                # headroom, so a stay now closes by the clock only on the
                # 0.265% of gaps that are genuinely unusual; every ordinary
                # one keeps the group open until a radius break closes it.
                #
                # `settled_seconds` keeps its other job untouched — it is
                # still the trip-settlement clock `trailing_fix_is_settled`
                # and `extract_trips` read above.
                new_stays.append(last_open_stay)
            elif elapsed_s < DWELL_CAP_MS / 1000:
                # Hold ANY open stay that has not yet settled — whether it
                # is sub-min-dwell (sparse arrival, issue #82) or past
                # min-dwell but not yet settled (issue #80). The old gate
                # held only when span >= min_dwell, which is exactly the
                # sparse-arrival shape that broke production: Life360's
                # resting cadence is one fix every 10-55 min, so each
                # tick sees a single new fix, the group's span stays
                # below min_dwell_ms, and the watermark advanced past
                # every fix — the group never grew, and a real overnight
                # home stay was consumed fix-by-fix and never written.
                #
                # The hold ceiling is DWELL_CAP_MS, NOT settled_seconds.
                # It is the same clock the close gate above now reads, so
                # the two branches partition the elapsed axis at one point
                # instead of two. DWELL_CAP_MS is the resting-cadence
                # ceiling: 31.7 min, measured against a live p99 inter-fix
                # gap of 30.2 min, so a fix arriving later than that is a
                # genuine break in observation rather than the ordinary
                # silence between two fixes of one stay, and holding past
                # it would only stall the watermark on a group whose fate
                # is already decided. (This once read "`_chunk_by_gap`
                # splits the batch wherever the gap exceeds DWELL_CAP_MS";
                # that stopped being true on 2026-09-05 when CHUNK_GAP_MS
                # became 12h, and the ceiling's justification is the
                # cadence measurement above, not the chunk split.)
                # settled_seconds (15 min) is
                # too short for this job: at a resting cadence of one
                # fix every 10-55 min, the clock runs past the group
                # between fixes, the watermark advances, and the next
                # fix starts a fresh one-fix group that can never reach
                # min_dwell — the issue #82 production shape. DWELL_CAP
                # (31.7 min) covers the longest inter-fix gap that can
                # still belong to one group, so holding to it keeps the
                # group readable until either the clock settles it
                # (span >= min_dwell AND elapsed >= settled_seconds
                # closes it above) or a radius break closes it.
                #
                # The cost is bounded: a subject who passes through and
                # never returns holds the watermark at most DWELL_CAP_MS
                # before the branch below advances it and the group is
                # dropped (span < min_dwell), so a lone drive-by fix
                # cannot stall the pipeline.
                stay_hold_id = next(
                    (
                        f.id for f in fixes
                        if f.ts >= last_open_stay.started_ts
                    ),
                    fixes[0].id,
                )
            # else: elapsed >= DWELL_CAP_MS/1000 but span < min_dwell_ms —
            # the group is not a stay (never reached the dwell bar) and
            # any future fix would start a new chunk anyway; advance the
            # watermark and let the group go. This is the pre-#82
            # behaviour for sub-min-dwell groups and it is correct: a
            # lone drive-by fix must not stall the pipeline.
        return new_stays, new_trips, trip_hold_id, stay_hold_id

    async def _place_label(
        self, planned: PlannedPlace, *, prior: DerivedPlace | None,
        new_address_by_started_ts: dict[int, str | None],
    ) -> str | None:
        """The display name for a planned place, in DEC-190's order: the
        household's own tag name; else a cached Google label (2026-08-19
        "Google-cache-only labelling"); else the Life360 mode address of its
        stays (issue #10 option 1); else the name a previous cycle wrote.

        The last step is a sticky keep, not a recomputation: only THIS
        cycle's stays carry an address in memory (`presence_stays` has no
        address column), so an address-less cycle is absence of new
        evidence, never evidence of no name."""
        if planned.tag_name is not None:
            return planned.tag_name
        label = await self._label_for(lat=planned.lat, lng=planned.lng)
        if label is not None:
            return label
        fallback = mode_address(
            s.address if s.address is not None
            else new_address_by_started_ts.get(s.started_ts)
            for s in planned.stays
        )
        if fallback is not None:
            return fallback
        # A tag that no longer claims the place must not leave its name on it.
        if prior is not None and prior.saved_place_id is None:
            return prior.label
        return None

    async def _reconcile_places(
        self, *, subject_id: str, circle_id: str, all_stays: list[Stay],
        new_stays: list[Stay], now_ms: int,
    ) -> None:
        """DEC-190 (design/27 §3.7, ADR 0014, issue #339): recompute the
        subject's whole place picture with `plan_places`, then make the store
        match it — save each planned place under the id it keeps, relink
        every stay whose link changed (overwriting, so a link can never
        outlive the place it pointed at), drop places nothing resolves to,
        and rebuild occupancy and rhythm for every place whose stay set
        changed.

        Writes are DIFFED against what is stored, so a steady-state cycle
        (same stays, same plan) writes nothing — the reconcile runs on every
        cycle that brings fixes, and must not rewrite the table each time."""
        existing = await self._store.places_for_subject(subject_id=subject_id)
        existing_by_id = {p.id: p for p in existing}
        tagged = await self._store.tagged_places()
        plan = plan_places(
            all_stays, existing=existing, tagged=tagged, now_ms=now_ms,
            tz_name=self._cfg.tz_name, cluster_eps_m=self._cfg.cluster_eps_m,
            cluster_min_samples=self._cfg.cluster_min_samples,
            match_radius_m=self._cfg.place_match_radius_m,
            min_nights=self._cfg.min_nights, min_weekdays=self._cfg.min_weekdays,
            label_freshness_days=self._cfg.label_freshness_days,
        )
        new_address_by_started_ts = {s.started_ts: s.address for s in new_stays}
        links: dict[int, int | None] = {}
        kept: set[int] = set()
        created: set[int] = set()
        for planned in plan:
            prior = (
                existing_by_id.get(planned.existing_id)
                if planned.existing_id is not None else None
            )
            label = await self._place_label(
                planned, prior=prior,
                new_address_by_started_ts=new_address_by_started_ts,
            )
            first_seen = min(s.started_ts for s in planned.stays)
            last_seen = max(s.ended_ts for s in planned.stays)
            unchanged = prior is not None and (
                prior.lat, prior.lng, prior.radius_m, prior.kind,
                prior.confidence, prior.label, prior.saved_place_id,
            ) == (
                planned.lat, planned.lng, planned.radius_m, planned.label.kind,
                planned.label.confidence, label, planned.saved_place_id,
            )
            if unchanged and planned.existing_id is not None:
                place_id = planned.existing_id
            else:
                place_id = await self._store.save_place(
                    subject_id=subject_id, circle_id=circle_id,
                    place_id=planned.existing_id,
                    lat=planned.lat, lng=planned.lng, radius_m=planned.radius_m,
                    kind=planned.label.kind, confidence=planned.label.confidence,
                    label=label, saved_place_id=planned.saved_place_id,
                    first_seen_ts=first_seen, last_seen_ts=last_seen,
                )
                if self._metrics is not None:
                    self._metrics.inc(
                        "nora_analytics_places_total",
                        subject=subject_id, kind=planned.label.kind,
                    )
            if planned.existing_id is None:
                created.add(place_id)
            kept.add(place_id)
            for s in planned.stays:
                links[s.started_ts] = place_id

        changed_links: dict[int, int | None] = {}
        dirty: set[int] = set(created)
        for s in all_stays:
            target = links.get(s.started_ts)
            if target != s.place_id:
                changed_links[s.started_ts] = target
                if target is not None:
                    dirty.add(target)
                if s.place_id is not None:
                    dirty.add(s.place_id)
        await self._store.relink_stays(subject_id=subject_id, links=changed_links)
        await self._store.delete_places(
            subject_id=subject_id,
            place_ids=sorted(pid for pid in existing_by_id if pid not in kept),
        )
        members: dict[int, list[Stay]] = {}
        for s in all_stays:
            target = links.get(s.started_ts)
            if target is not None:
                members.setdefault(target, []).append(s)
        for place_id in sorted(dirty & kept):
            await self._rebuild_rhythm(
                subject_id=subject_id, place_id=place_id,
                stays=members.get(place_id, []), now_ms=now_ms,
            )
        if new_stays and self._metrics is not None:
            # Anomalies are a pure function of the stays and their links, so
            # they are computed on read (`detect_stay_anomalies`), never
            # stored — a stored copy would go stale on every relink. The
            # rollup only COUNTS the ones this cycle's stays carry, scored
            # against the links it just wrote.
            linked = [
                dataclasses.replace(s, place_id=links.get(s.started_ts))
                for s in all_stays
            ]
            by_start = {s.started_ts: s for s in linked}
            for new in new_stays:
                stay = by_start.get(new.started_ts)
                if stay is None:
                    continue
                for anomaly in detect_stay_anomalies(
                    stay, history=linked, tz_name=self._cfg.tz_name,
                    min_samples=self._cfg.anomaly_min_samples,
                    z_threshold=self._cfg.anomaly_z_threshold,
                    novelty_radius_m=self._cfg.cluster_eps_m,
                ):
                    self._metrics.inc(
                        "nora_analytics_anomalies_total",
                        subject=subject_id, kind=anomaly.kind,
                    )

    async def _rebuild_rhythm(
        self, *, subject_id: str, place_id: int, stays: list[Stay], now_ms: int,
    ) -> None:
        """Tier 3 (design/27 §4, issue #6) for ONE place, recomputed wholesale
        from the stays linked to it: occupancy (dwell per local hour-of-week)
        and rhythm (circular mean arrival/departure, duration median/MAD).
        Full recompute rather than accumulation, so it is idempotent and
        follows a relink — accumulation is what left dwell on a place after
        its stays had moved."""
        bins: dict[int, int] = {}
        for s in stays:
            for how, dwell in hour_of_week_bins(
                s.started_ts, s.ended_ts, self._cfg.tz_name,
            ).items():
                bins[how] = bins.get(how, 0) + dwell
        await self._store.replace_occupancy(
            subject_id=subject_id, place_id=place_id, bins=bins, now_ms=now_ms,
        )
        if self._metrics is not None:
            self._metrics.inc(
                "nora_analytics_occupancy_bins_total",
                value=len(bins), subject=subject_id,
            )
        if not stays:
            return
        tz = ZoneInfo(self._cfg.tz_name)
        durations = sorted(float(s.ended_ts - s.started_ts) for s in stays)
        median_ms = _median(durations)
        mad_ms = _median(sorted(abs(d - median_ms) for d in durations))
        await self._store.upsert_rhythm(
            subject_id=subject_id, place_id=place_id,
            mean_arrival_minute=circular_mean_minute(
                [_local_minute_of_day(s.started_ts, tz) for s in stays]
            ),
            mean_departure_minute=circular_mean_minute(
                [_local_minute_of_day(s.ended_ts, tz) for s in stays]
            ),
            duration_median_ms=int(median_ms), duration_mad_ms=int(mad_ms),
            sample_count=len(stays), now_ms=now_ms,
        )

    async def _record_corridors(self, *, subject_id: str, now_ms: int) -> None:
        """Tier 5 (design/27 §4, issue #6): cluster this subject's
        NOT-YET-LINKED trips into corridors, give each corridor a durable
        identity, and recompute the touched corridors' hour-of-week speed
        percentiles.

        **The set clustered here is the accumulated pool, not this cycle's
        batch** — `unclustered_named_trips_for_subject` reads back every
        trip with a road-name sequence whose `corridor_id` is still NULL,
        newest first, bounded by `corridor_cluster_batch_limit`. That is
        the whole point of the redesign. Clustering only the trips
        extracted in the current tick meant `corridor_min_samples` similar
        trips had to arrive inside ONE ~120 s tick, and in real deployment
        each occurrence of a real route (a daily commute) arrives in its
        own separate tick — so `cluster_trips` always saw a batch of one
        and dropped it as noise. Tier 5 would have shipped minting zero
        corridors and zero speed percentiles, silently, forever (final
        whole-branch review, Critical #1). Now an occurrence of a route
        waits in the pool for its siblings, however many days apart they
        fall. Measured both ways on the same fixture — a 6-day/4324-tick
        repeating commute driven through the real `run_once` loop gives 60
        trips / 0 corridors / 0 speed rows with per-batch clustering, and
        60 / 10 / 60 with this
        (`tests/unit/test_analytics_rollup.py::
        test_a_repeating_commute_actually_grows_corridors_over_days`).

        **Still no `last_trip_cluster_id` watermark column, and now for a
        positive reason rather than the absence of a code path**:
        `presence_trips.corridor_id IS NULL` already IS the durable "not
        yet processed" marker, and it is a strictly better one than a
        watermark id — it is per-trip, so a trip that fails to link stays
        eligible next cycle instead of being skipped past. This is the same
        shape `presence_stays.place_id IS NULL` already plays for Tier 3's
        first-place backfill.

        **Gated on new trips arriving** — the only caller runs it inside
        `run_once`'s `if new_trips:` branch, after `write_trips` lands. Not
        because re-clustering an unchanged pool would be WRONG (it is
        idempotent: same trips, same corridors, same answer) but because it
        would be pure waste at tick cadence, and expensive waste — see the
        cost table below. A new trip is precisely the event that can push a
        group over `corridor_min_samples` or bring a match into range, so
        nothing is ever delayed by more than one arriving trip. The one
        thing the gate does defer is a CONFIG change (a lowered
        `corridor_min_samples` takes effect on the next trip, not on the
        next tick), which is the same bargain DEC-115's `last_cluster_id`
        dirty-set gate already strikes for `cluster_places`.

        The cost is why the gate and the bound both exist. `cluster_trips`
        is O(n^2) in an edit-distance metric that is itself expensive.
        Measured on this machine (2026-08-20; n trips over 6 recurring
        corridors of 6-10 road names, jittered — the same corpus shape as
        the pre-redesign table this replaces, and reproducing it closely):

            cluster_trips  n=66     31 ms     n=600    2.67 s
            cluster_trips  n=200   296 ms     n=1000   7.59 s
            cluster_trips  n=400  1.19 s      n=2000  30.6 s
            cluster_trips  n=500  1.87 s      n=3600  ~92 s (extrapolated)
            cluster_places n=3600  6.0 s

        `cluster_places`' docstring calls its own O(n^2) "trivial" at 3,600
        stays and it is (6 s, gated). `cluster_trips` at the same n is ~15x
        worse, because an edit distance over two 6-10 element sequences is
        hundreds of Python-level operations where a haversine is a handful.
        `corridor_cluster_batch_limit` defaults to 500 for a stated budget
        — under 2% of a `tick_seconds=120` tick for one subject (see that
        field's own comment for the arithmetic).

        Cross-cycle continuity ALSO still comes from `existing_corridor`'s
        similarity match against the persisted corridor sequences (pass 2
        below); the pool is what lets a corridor be BORN, the similarity
        match is what lets it keep growing once it exists.
        """
        # The address-less hold-out is enforced by the QUERY, not here.
        # `edit_distance((), x) == len(x)` makes an empty sequence a
        # similarity-neighbour of every 1-2 element real corridor, so
        # `cluster_trips` will legitimately put an address-less trip in the
        # same cluster as three real, named ones — it is not merely
        # clusterable with other address-less trips. Judging that cluster by
        # its FIRST trip's sequence is a coin-flip on input order, wrong in
        # both directions: an empty `cluster[0]` discards the whole cluster
        # and silently unlinks the real trips in it, and a named `cluster[0]`
        # attaches the empty one too, contaminating the corridor's
        # `sample_count` and its speed percentiles with an ungrounded sample
        # (Task 11 review, Important #1). Task 11 partitioned in this method;
        # the filter moved into `unclustered_named_trips_for_subject`'s
        # `jsonb_array_length(sequence) > 0` when the pool became a query, so
        # there is ONE place it is enforced and the contract suite holds both
        # store implementations to it
        # (`test_unclustered_named_trips_excludes_linked_and_address_less`).
        pool = await self._store.unclustered_named_trips_for_subject(
            subject_id=subject_id,
            limit=self._cfg.corridor_cluster_batch_limit,
        )
        clusters = cluster_trips(
            pool, max_edit_distance=self._cfg.corridor_max_edit_distance,
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
        created: set[int] = set()
        clustered_starts = {t.started_ts for cluster in clusters for t in cluster}
        for cluster in clusters:
            # The cluster's FIRST trip's own sequence is the
            # representative — `PRESENCE_CORRIDORS`' own "identity
            # survives, does not chase every new trip's own sequence". The
            # pool arrives newest-first, so that representative is the most
            # RECENTLY observed spelling of the route, which is the better
            # of the two arbitrary choices.
            attached = await self._attach_corridor(
                subject_id=subject_id, sequence=cluster[0].corridor,
                trips=cluster, now_ms=now_ms, may_create=True,
            )
            if attached is not None:
                corridor_id, canonical, was_created = attached
                touched[corridor_id] = canonical
                if was_created:
                    created.add(corridor_id)
        # A trip `cluster_trips` called noise can still belong to a corridor
        # this subject ALREADY has — and it usually does. Once a corridor
        # exists, each new occurrence of that route arrives alone in its own
        # tick and is a singleton for clustering purposes. Without this
        # second pass a corridor's `sample_count` and its speed percentiles
        # would freeze at whatever minted it, and Tier 5 would look built
        # while silently never learning again. Creating from a singleton is
        # still refused — that is `corridor_min_samples`' whole job.
        #
        # Grouped by EXACT sequence rather than walked trip by trip: the
        # leftovers are dominated by repeats of the same route, `existing_
        # corridor` would return the identical answer for each of them, and
        # this pass now runs over an accumulated pool of up to
        # `corridor_cluster_batch_limit` trips rather than one tick's
        # handful — so the naive loop would be up to that many store
        # round-trips per tick. Grouping also makes the linking one
        # `set_trip_corridor_ids` call per corridor instead of one per trip.
        leftovers: dict[tuple[str, ...], list[Trip]] = {}
        for trip in pool:
            if trip.started_ts in clustered_starts:
                continue
            leftovers.setdefault(trip.corridor, []).append(trip)
        for sequence, trips in leftovers.items():
            attached = await self._attach_corridor(
                subject_id=subject_id, sequence=sequence,
                trips=trips, now_ms=now_ms, may_create=False,
            )
            if attached is not None:
                corridor_id, canonical, was_created = attached
                touched[corridor_id] = canonical
                if was_created:
                    created.add(corridor_id)
        if self._metrics is not None:
            # ONE increment per distinct corridor per pass, labelled by
            # outcome — not one per attach operation, which is what this
            # counted before and why "no corridor has ever been created"
            # was unreadable from it (final whole-branch review, Important
            # #2). `created` is decided by `_attach_corridor` from whether
            # `existing_corridor` matched, so it is the real thing and not
            # a guess from the count.
            for corridor_id in sorted(touched):
                self._metrics.inc(
                    "nora_analytics_corridors_total", subject=subject_id,
                    outcome="created" if corridor_id in created else "reconfirmed",
                )
        for corridor_id, sequence in sorted(touched.items()):
            await self._record_corridor_speeds(
                subject_id=subject_id, corridor_id=corridor_id,
                sequence=sequence, now_ms=now_ms,
            )

    async def _attach_corridor(
        self, *, subject_id: str, sequence: tuple[str, ...], trips: list[Trip],
        now_ms: int, may_create: bool,
    ) -> tuple[int, tuple[str, ...], bool] | None:
        """Link `trips` to the corridor `sequence` names, creating it when
        `may_create` and no near-match exists. Returns `(corridor_id,
        canonical_sequence, created)`, or `None` when nothing was attached.

        `created` — whether this call INSERTed a brand-new corridor row
        rather than reconfirming one that already existed — is returned
        rather than counted here, because the counting has to be per
        corridor and this method is called once per cluster/leftover group.
        See `_record_corridors`' own increment."""
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
        return corridor_id, canonical, existing_id is None

    async def _record_corridor_speeds(
        self, *, subject_id: str, corridor_id: int, sequence: tuple[str, ...],
        now_ms: int,
    ) -> None:
        """Per-hour-of-week p50/p90 over EVERY trip ever linked to this
        corridor — a full recompute from the whole sample set, never an
        incremental update, the same posture `_rebuild_rhythm` takes."""
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

def _median(ordered: list[float]) -> float:
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


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
