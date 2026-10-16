"""The presence Rollup as a fold over the Record.

**A pure function of the Record and a logical watermark.** `run_once` reads
one person's Record events — their fixes, and the presence collector's
successful polls of their circles — strictly after where the fold last stood
(`FoldState.position`) and at or before the watermark `through_ms`, in one
canonical order (`EventPosition`), and folds them ONE AT A TIME through
`_step`. A rebuild at watermark W is the same fold from the start of the
Record. Because every event is folded alone, in the same order, whatever the
runs were, the incremental result at W and the rebuild at W are the same
rows by construction (`tests/unit/test_rollup_determinism.py` is the gate).

**No wall clock.** The fold's clock is `FoldState.logical_now_ms`: the newest
time the Record has shown — a fix's `ts`, a successful poll's `at_ms`. A stay
closes when a later fix, or a recorded poll proving the time passed with no
fix, shows it ended. The wall clock enters exactly once, in the
daemon, to choose WHICH watermark to build to (`now - grace_seconds`, so a
row committed late is still inside it); it never enters what is computed at
that watermark.

**Why the clock is logical.** Deciding a tail (an open stay's close, a trip's
settlement, a provisional arrival's hold, a place label's staleness) on wall
time makes a tick-by-tick run and a batch re-derive disagree: the first
evaluates it between every pair of fixes, the second once at a `now` long
after every fix, so every tail inside the history would be decided by the NEXT
FIX instead. A quiet phone at home (one fix, then 35 minutes of silence, then
a fix at the same place) would be two stays tick by tick and one in the batch.
The fold decides each of these on the Record's own clock, at each event, in
both paths.

Places, occupancy, rhythm and corridors are recomputed from the fold's
stays and trips (`_derive`) whenever those change, and a place's label is
re-checked on the logical clock every run; each is a function of the stays
and trips, the saved places as of the watermark and the logical clock, so
it too is the same in both paths. Surrogate ids (a place's, a corridor's)
are kept stable by overlap within a build and differ between builds; what
cites them cites `(kind, subject, interval)`.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
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
    collapse_duplicate_fixes,
    edit_distance,
    extract_trips,
    hour_of_week_bins,
    label_place,
    mode_address,
    plan_places,
    segment_stays,
    trailing_fix_is_settled,
)
from nora.domain.mobility_forecast import detect_stay_anomalies
from nora.domain.ports import GeocodeCacheStore
from nora.domain.rollup import (
    Event,
    EventPosition,
    FixEvent,
    FoldState,
    PollEvent,
    StoredCorridor,
    tags_sha256,
)

log = logging.getLogger("nora.analytics.rollup")


class MobilityStore(Protocol):
    """The presence Rollup store, bound to ONE build for writing
    (`SqlMobilityStore.scoped`); readers use it unbound and read the current
    build. Bound by `tests/contract/test_mobility_store_contract_synthetic.py`."""

    # -- the fold
    async def fold_state(self, *, subject_id: str) -> FoldState | None: ...
    async def events_since(
        self, *, person_ids: Sequence[str], circle_ids: Sequence[str],
        after: EventPosition, through_ms: int, limit: int,
    ) -> list[Event]: ...
    async def fixes_by_id(self, *, ids: Sequence[int]) -> list[RawFix]: ...
    async def commit_fold(
        self, *, subject_id: str, stays: list[Stay], trips: list[Trip],
        state: FoldState,
    ) -> None: ...
    async def mark_derived(self, *, subject_id: str, tags_sha256: str) -> None: ...
    # -- the derived rows of the bound build
    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]: ...
    async def trips_for_subject(self, *, subject_id: str) -> list[Trip]: ...
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
    async def replace_occupancy(
        self, *, subject_id: str, place_id: int, bins: dict[int, int], now_ms: int
    ) -> None: ...
    async def upsert_rhythm(
        self, *, subject_id: str, place_id: int, mean_arrival_minute: float,
        mean_departure_minute: float, duration_median_ms: int,
        duration_mad_ms: int, sample_count: int, now_ms: int,
    ) -> None: ...
    async def corridors_for_subject(self, *, subject_id: str) -> list[StoredCorridor]: ...
    async def save_corridor(
        self, *, subject_id: str, corridor_id: int | None,
        sequence: tuple[str, ...], sample_count: int, first_seen_ts: int,
        last_seen_ts: int,
    ) -> int: ...
    async def delete_corridors(self, *, subject_id: str, corridor_ids: list[int]) -> None: ...
    async def relink_trips(
        self, *, subject_id: str, links: dict[int, int | None],
    ) -> None: ...


@dataclass(frozen=True)
class RollupConfig:
    tick_seconds: float
    safety_lag: int
    settled_seconds: float
    max_age_seconds: float
    #: How many Record events one read returns; the fold pages through a
    #: backlog of any size, one event at a time, so this bounds memory, never
    #: what is computed.
    batch_limit: int
    #: Kept so a config naming them still loads; the fold reads no batch to
    #: widen and has no backlog threshold (an undecided group simply stays in
    #: the window).
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
    # DEC-190's match radius: a tagged place claims a stay or a cluster this
    # close. Its OWN constant, never `cluster_eps_m`: the match band sits far
    # below the nearest non-match, so anything in 100-500 m is safe and 50 m
    # is not.
    place_match_radius_m: float = 150.0
    # How far a NON-driving fix may sit from a stay's centre and still be
    # part of it (time only, never position) — `segment_stays`' docstring.
    # Sized to hold a long stop's resting fixes, which wander a few hundred
    # metres. `None` disables it.
    stay_wander_radius_m: float | None = 400.0


@dataclass(frozen=True)
class FoldRun:
    """What one `run_once` did, for the unit rollups and the stimuli above it:
    how many events it consumed, the stays it closed, the stay in progress
    before and after, and the logical clock it reached."""

    subject_id: str
    events: int
    new_stays: tuple[Stay, ...]
    open_before: Stay | None
    open_after: Stay | None
    logical_now_ms: int
    anomalous: tuple[Stay, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.new_stays) or self.open_before != self.open_after


class MobilityRollup:
    def __init__(
        self, *, store: MobilityStore, config: RollupConfig, metrics: Any = None,
        geocode_cache: GeocodeCacheStore | None = None,
    ) -> None:
        self._store = store
        self._cfg = config
        self._metrics = metrics
        # Google-cache-only labelling: optional — a place with no nearby
        # cached hit stays unlabelled rather than triggering a proactive
        # Google call. Like the saved places, the cache is read as the store
        # holds it when the run reads it: an input that is not an event of
        # the fold (a rebuild as of an earlier T reads both from a snapshot
        # at T).
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

    async def run_once(
        self, *, subject_id: str, person_ids: list[str], circle_id: str,
        through_ms: int, circle_ids: Sequence[str] | None = None,
    ) -> FoldRun:
        """Fold every Record event for this person up to `through_ms`, then
        derive what changed. Idempotent: a second call at the same watermark
        reads no event and writes nothing."""
        circles = tuple(circle_ids) if circle_ids else (circle_id,)
        state = await self._store.fold_state(subject_id=subject_id) or FoldState()
        window = await self._store.fixes_by_id(ids=list(state.window_ids))
        open_before = state.open_stay
        new_stays: list[Stay] = []
        new_trips: list[Trip] = []
        consumed = 0
        page = max(1, self._cfg.batch_limit)
        while True:
            events = await self._store.events_since(
                person_ids=person_ids, circle_ids=circles, after=state.position,
                through_ms=through_ms, limit=page,
            )
            for event in events:
                state, window, stays, trips = self._step(
                    state, window, event, subject_id=subject_id,
                    person_ids=person_ids, circle_id=circle_id,
                )
                new_stays.extend(stays)
                new_trips.extend(trips)
            consumed += len(events)
            if len(events) < page:
                break
        if consumed:
            state = dataclasses.replace(
                state, window_ids=tuple(f.id for f in window),
                tags_sha256=None if (new_stays or new_trips) else state.tags_sha256,
            )
            await self._store.commit_fold(
                subject_id=subject_id, stays=new_stays, trips=new_trips, state=state,
            )
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_analytics_stays_total", value=len(new_stays), subject=subject_id,
                )
                if new_trips:
                    self._metrics.inc(
                        "nora_analytics_trips_total", value=len(new_trips),
                        subject=subject_id,
                    )
        anomalous = await self._derive(
            subject_id=subject_id, circle_id=circle_id, state=state,
            new_stays=new_stays, advanced=bool(consumed),
        )
        return FoldRun(
            subject_id=subject_id, events=consumed, new_stays=tuple(new_stays),
            open_before=open_before, open_after=state.open_stay,
            logical_now_ms=state.logical_now_ms, anomalous=tuple(anomalous),
        )

    # -- the fold -----------------------------------------------------------

    def _step(
        self, state: FoldState, window: list[RawFix], event: Event, *,
        subject_id: str, person_ids: list[str], circle_id: str,
    ) -> tuple[FoldState, list[RawFix], list[Stay], list[Trip]]:
        """Fold ONE event. A pure function of the state, the window and the
        event: the whole determinism argument rests on this being so.

        A fix joins the window (or, if it is older than what the fold has
        already finalized, is dropped as late: a stay once written is never
        re-cut by a straggler) and the window is evaluated. A poll only moves
        the clock, and the window is evaluated only when the move crosses
        one of the clock thresholds a decision reads (`_deadline_crossed`):
        every other poll would change nothing, and skipping it is what keeps
        a rebuild over months of polls affordable."""
        if isinstance(event, FixEvent):
            fix = event.fix
            now = max(state.logical_now_ms, fix.ts)
            if fix.ts <= state.frontier_ts:
                return (
                    dataclasses.replace(
                        state, position=event.position, logical_now_ms=now,
                        late_fixes=state.late_fixes + 1,
                    ),
                    window, [], [],
                )
            window = [*window, fix]
        elif isinstance(event, PollEvent):
            now = max(state.logical_now_ms, event.at_ms)
            if not window or not self._deadline_crossed(
                window, before=state.logical_now_ms, after=now,
            ):
                return (
                    dataclasses.replace(state, position=event.position, logical_now_ms=now),
                    window, [], [],
                )
        else:
            raise TypeError(f"not a Record event: {event!r}")
        ordered = sorted(window, key=lambda f: (f.ts, f.id))
        stays, trips, trip_hold_id, stay_hold_id, open_stay = self._segment_batch(
            ordered, subject_id=subject_id, person_ids=person_ids,
            circle_id=circle_id, now_ms=now,
        )
        # What stays undecided is everything from the earliest held fix ON,
        # in TIME order — never «every id above a mark»: a fix's id is when
        # its row was written, and the fold's window is a stretch of time
        # (a late upload has a higher id than the fixes after it). Trimming
        # by id would keep a closed stay's fixes in the window and write the
        # stay again at every later event.
        position = {f.id: i for i, f in enumerate(ordered)}
        holds = [position[h] for h in (trip_hold_id, stay_hold_id) if h is not None]
        kept = ordered[min(holds):] if holds else []
        frontier = max(
            [state.frontier_ts, *(s.ended_ts for s in stays), *(t.ended_ts for t in trips)]
        )
        return (
            dataclasses.replace(
                state, position=event.position, logical_now_ms=now,
                frontier_ts=frontier, open_stay=open_stay,
            ),
            kept, stays, trips,
        )

    def _deadline_crossed(self, window: list[RawFix], *, before: int, after: int) -> bool:
        """Does moving the clock from `before` to `after` cross a threshold
        any decision of `_segment_batch` reads? Every such threshold is a
        window fix's `ts` plus `settled_seconds` (trip settlement) or plus
        `DWELL_CAP_MS` (a stay's close and hold, the arrival hold).
        `max_trip_duration_seconds` is measured between fixes, not against
        the clock, so no poll can cross it."""
        spans = (int(self._cfg.settled_seconds * 1000), DWELL_CAP_MS)
        return any(
            before < f.ts + span <= after for f in window for span in spans
        )


    def _segment_batch(
        self, fixes: list[Any], *, subject_id: str, person_ids: list[str],
        circle_id: str, now_ms: int,
    ) -> tuple[list[Stay], list[Trip], int | None, int | None, Stay | None]:
        """One evaluation of the fold's window: `(new_stays, new_trips,
        trip_hold_id, stay_hold_id, open_stay)`. Pure with respect to the
        store: the fold calls it after every event that could change a
        decision (`_step`). `now_ms` is the fold's LOGICAL clock — the
        newest time the Record has shown for this person (a fix's `ts`, a
        successful poll's `at_ms`) — never the wall clock; every
        «elapsed» below is measured against it. `open_stay` is the trailing
        stay in progress once it has dwelled `min_dwell` and the clock has
        not closed it."""
        new_stays: list[Stay] = []
        new_trips: list[Trip] = []
        raw = collapse_duplicate_fixes([
            RawFix(
                id=f.id, ts=f.ts, lat=f.lat, lng=f.lng, address=f.address,
                is_driving=f.is_driving,
            )
            for f in fixes
        ])
        min_dwell_ms = int(self._cfg.stay_min_dwell_seconds * 1000)
        # `segment_stays` is purely spatial: fixes at (near-)identical
        # coordinates never break its radius test however large the time gap,
        # so a subject who returns to the same place night after night would
        # merge every visit into ONE long "stay" whose recorded dwell
        # (`ended_ts - started_ts`) includes the away-time. That dilutes
        # `label_place`'s TIME-WEIGHTED night/work share below any threshold,
        # so a subject home every night would never label as `home`.
        # Splitting the batch wherever the gap between consecutive fixes
        # exceeds `CHUNK_GAP_MS` and segmenting each chunk independently
        # yields the SEPARATE per-day stays `label_place` and `cluster_places`
        # expect.
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
                wander_radius_m=self._cfg.stay_wander_radius_m,
            )
            new_stays.extend(seg.closed)
            # Trips are the complement of stays over the SAME chunks the stay
            # segmentation just walked; re-chunking here could only give a
            # different answer, never a better one.
            chunk_closed = list(seg.closed)
            open_started: int | None = None
            is_final_chunk = i == len(chunks) - 1
            # Trip settlement. `segment_stays` ALWAYS returns a trailing
            # `open_stay` for a non-empty window (whatever remains in
            # `group`, even one fix at 90 km/h). Cutting `extract_trips` at
            # that open stay unconditionally would flush "everything except
            # the newest fix" as a finished trip on every tick, and a
            # continuous drive would come out as one trip per tick.
            #
            # `tail_settled` is `trailing_fix_is_settled` — the SAME
            # predicate, on the SAME fix (`chunk[-1]` is `extract_trips`' own
            # `buffer[-1]` for the final chunk), that `extract_trips` uses to
            # decide whether to finalize. If the two disagreed, the same
            # fixes would be held back as candidate TRIP fixes here and
            # written as a STAY below, landing in both tables.
            #
            # An unsettled tail has no stay-in-progress to cut at and its
            # fate is undetermined: `open_stay_started_ts=None`.
            # `last_open_stay` is likewise left unset, so a group being held
            # back is not also closable as a stay by the `settled_seconds`
            # block below.
            #
            # `None` is NOT a synonym for `False` here. `True` (still
            # driving) is never settled by the clock. `False` is a real
            # arrival: the parked tail IS a stay-in-progress. `None` is no
            # reading at all (every historical fix, anything Life360 omits
            # the flag on) and falls back to `settled_seconds`, the
            # quiet-boundary clock a stay's arrival uses.
            tail_settled = trailing_fix_is_settled(
                chunk[-1], now_ms=now_ms,
                settled_seconds=self._cfg.settled_seconds,
            )
            # `tail_dwelled` is the SECOND gate; the clock alone is not
            # enough. At a RESTING cadence (Life360 emits every ~10 min when
            # still) the trailing fix is never older than `settled_seconds`,
            # so a subject sitting at home all day would have their whole day
            # held back as "undecided movement", and when it finally flushed
            # their stationary fixes would come out as 0 km/h, 0 m trips
            # instead of a stay.
            #
            # The discriminator is the trailing group's own DWELL. A resting
            # tail is one spatially-coherent group whose span passes
            # `min_dwell_ms` — the same bar `_close` uses to call something a
            # stay. A driving tail is a fix that just broke the radius, so
            # its group's span is ~0. A tail that has already dwelled past
            # `min_dwell_ms` is therefore the stay-in-progress it looks like,
            # whatever the `is_driving` flag says: the flag is NOISY AT REST
            # (people sitting at home produce `is_driving=True` fixes), and
            # letting it win would leave a genuine home stay never settled,
            # never closed, and its fixes watermark-consumed forever.
            # The gridlock false-positive this admits (a real drive stuck in
            # traffic whose tail dwells past the bar) is the accepted trade:
            # it writes ONE stay, once, and the watermark advances past it.
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
                    # extraction too. `seg.closed` alone would omit it, and
                    # `extract_trips` would read a genuine stay's own fixes as
                    # movement and write a ~0 km/h "trip"; the sub-min-dwell
                    # case correctly stays out, since `segment_stays`
                    # discarded that group too.
                    chunk_closed.append(seg.open_stay)
            # Only the FINAL chunk gets the settlement args. A non-final
            # chunk's trailing group ended because an OBSERVED gap followed
            # it, so its fate is already decided and there is nothing to
            # settle; `now_ms=None` makes `extract_trips` flush it
            # unconditionally (its docstring).
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
            # ARRIVAL RACE: the trip was cut at the start of a trailing group
            # that has not yet dwelled `min_dwell_ms` — a PROVISIONAL stop.
            # The next fix often jitters it apart and the stay starts a few
            # fixes later, so the fixes between the already-written trip and
            # the real stay would come out as a second, orphan "trip" of a
            # few hundred metres at walking pace: one phantom per arrival.
            # While the stop is unconfirmed (and still inside the
            # resting-cadence clock), hold the trip that runs into it as
            # well; it is cut for good once the stop confirms, or rejoins the
            # movement if the stop dissolves.
            #
            # When the movement before the provisional stop is still too
            # short to be a trip (the fold evaluates after EVERY fix, so a
            # stop is provisional at the first fix of every arrival), hold
            # those fixes too rather than discarding them as noise: a sparse
            # drive of two fixes 30 minutes apart becomes a trip once its
            # arrival confirms or the clock runs out.
            provisional_stop = (
                open_started is not None
                and seg.open_stay is not None
                and seg.open_stay.ended_ts - seg.open_stay.started_ts < min_dwell_ms
                and (now_ms - chunk[-1].ts) / 1000 < DWELL_CAP_MS / 1000
            )
            if (
                provisional_stop and chunk_trips
                and not any(c.started_ts > chunk_trips[-1].ended_ts for c in chunk_closed)
            ):
                provisional = chunk_trips.pop()
                held_back = held_back or [
                    f for f in chunk if f.ts >= provisional.started_ts
                ]
            elif provisional_stop and not held_back and open_started is not None:
                settled_end = max((c.ended_ts for c in chunk_closed), default=None)
                last_trip_end = chunk_trips[-1].ended_ts if chunk_trips else None
                floor = max(
                    (x for x in (settled_end, last_trip_end) if x is not None),
                    default=None,
                )
                held_back = [
                    f for f in chunk
                    if f.ts < open_started and (floor is None or f.ts > floor)
                ]
            new_trips.extend(chunk_trips)
            if held_back:
                # The EARLIEST held fix (held_back is in time order): the
                # fold keeps its window from there, by position in time.
                trip_hold_id = held_back[0].id
        # An open stay closes THIS evaluation only once DWELL_CAP_MS has
        # elapsed since its last fix, on the fold's LOGICAL clock — a later
        # fix or a recorded poll proving the time passed. That is a decision
        # this evaluation makes, not `segment_stays`.
        #
        # If it does NOT close this cycle, its fixes must stay readable:
        # were the watermark to advance past them, the next cycle would
        # re-segment only the NEW fixes, the open stay's true arrival time
        # would be lost, and when the subject finally leaves, the radius
        # break that would close the home group would never fire (the home
        # fixes are no longer in the batch) — the stay is silently dropped.
        # This is the stay-side counterpart of the trip hold above: hold the
        # watermark to the fix immediately before the open stay's first fix,
        # so the whole group is re-read next cycle until its fate is decided
        # (radius break closes it, or the clock settles it).
        stay_hold_id: int | None = None
        if last_open_stay is not None:
            elapsed_s = (now_ms - last_open_stay.ended_ts) / 1000
            span_ms = last_open_stay.ended_ts - last_open_stay.started_ts
            if (
                elapsed_s >= DWELL_CAP_MS / 1000
                and span_ms >= min_dwell_ms
            ):
                # The close clock is DWELL_CAP_MS (~31.7 min), NOT
                # `settled_seconds` (15 min), and it is deliberately the SAME
                # clock the hold branch below uses. At a resting cadence the
                # gap between two fixes of ONE stay runs up to ~30 min, so 15
                # min of silence is not evidence the stay ended: closing on
                # it would cut a stay at 2 fixes (they clear `min_dwell_ms`)
                # and start a fresh one at the next fix, fragmenting stays
                # into cadence-sized pieces and feeding Tier 3 a degenerate
                # anomaly baseline (tiny duration MADs). DWELL_CAP_MS sits
                # just above the longest ordinary inter-fix gap, so a stay
                # closes by the clock only on a genuinely unusual gap;
                # otherwise the group stays open until a radius break closes
                # it. One clock, read in both branches, partitions the
                # elapsed axis at one point.
                #
                # `settled_seconds` keeps its other job: the trip-settlement
                # clock `trailing_fix_is_settled` and `extract_trips` read
                # above.
                new_stays.append(last_open_stay)
            if elapsed_s >= DWELL_CAP_MS / 1000:
                # Resolved by the clock (closed above, or dropped as
                # sub-min-dwell). If `segment_stays` left the group open
                # because its LAST fix broke the radius, that undecided fix
                # sits after the group's end and belongs to neither the stay
                # nor a trip. Consuming it would drop the first fix of the
                # next place, recording an arrival at resting cadence one fix
                # gap late. Hold the watermark AT it, so the next cycle
                # starts the next group from it.
                undecided = [f.id for f in fixes if f.ts > last_open_stay.ended_ts]
                if undecided:
                    stay_hold_id = undecided[0]
            elif elapsed_s < DWELL_CAP_MS / 1000:
                # Hold ANY open stay that has not yet settled — sub-min-dwell
                # (sparse arrival) or past min-dwell but not yet settled.
                # Life360's resting cadence is one fix every 10-55 min, so
                # each tick sees a single new fix and the group's span stays
                # below `min_dwell_ms`; advancing the watermark past every
                # fix would mean the group never grew and a real overnight
                # home stay was consumed fix-by-fix and never written.
                #
                # The hold ceiling is DWELL_CAP_MS, NOT settled_seconds: the
                # same clock the close gate above reads. It is the
                # resting-cadence ceiling: a fix arriving later than that is a
                # genuine break in observation rather than the ordinary
                # silence between two fixes of one stay, and holding past it
                # would only stall the watermark on a group whose fate is
                # already decided. `settled_seconds` (15 min) is too short:
                # the clock would run past the group between fixes, the
                # watermark would advance, and the next fix would start a
                # fresh one-fix group that can never reach min_dwell.
                #
                # The cost is bounded: a subject who passes through and never
                # returns holds the watermark at most DWELL_CAP_MS before the
                # branch below advances it and the group is dropped
                # (span < min_dwell), so a lone drive-by fix cannot stall the
                # pipeline.
                stay_hold_id = next(
                    (
                        f.id for f in fixes
                        if f.ts >= last_open_stay.started_ts
                    ),
                    fixes[0].id,
                )
            # else: elapsed >= DWELL_CAP_MS/1000 but span < min_dwell_ms —
            # the group is not a stay (never reached the dwell bar) and any
            # future fix would start a new chunk anyway; advance the
            # watermark and let the group go: a lone drive-by fix must not
            # stall the pipeline.
        open_stay: Stay | None = None
        if (
            last_open_stay is not None
            and (now_ms - last_open_stay.ended_ts) / 1000 < DWELL_CAP_MS / 1000
            and last_open_stay.ended_ts - last_open_stay.started_ts >= min_dwell_ms
        ):
            open_stay = last_open_stay
        return new_stays, new_trips, trip_hold_id, stay_hold_id, open_stay

    # -- what the stays and trips add up to ---------------------------------

    async def _derive(
        self, *, subject_id: str, circle_id: str, state: FoldState,
        new_stays: list[Stay], advanced: bool,
    ) -> list[Stay]:
        """Places, occupancy, rhythm and corridors, as a function of the
        fold's stays and trips, the saved places and the fold's clock. The
        full plan runs when the stays or trips changed (the fold cleared
        `tags_sha256`) or the saved places did; otherwise only
        the labels are re-checked, because a label is the one thing the clock
        alone moves (`label_freshness_days`). Returns the new stays the
        rhythm scores anomalous.

        A failure here is logged and left for the next run: the fold's rows
        are already committed, and `tags_sha256` stays cleared until a plan
        succeeds, so the next run plans again rather than skipping it."""
        if not advanced and state.tags_sha256 is not None:
            return []
        now = state.logical_now_ms
        try:
            tags = await self._store.tagged_places()
            digest = tags_sha256(tags)
            all_stays = await self._store.stays_for_subject(subject_id=subject_id)
            if state.tags_sha256 == digest:
                await self._recheck_labels(
                    subject_id=subject_id, all_stays=all_stays, now_ms=now,
                )
                return []
            anomalous = await self._reconcile_places(
                subject_id=subject_id, circle_id=circle_id, all_stays=all_stays,
                new_stays=new_stays, tags=tags, now_ms=now,
            )
            await self._derive_corridors(subject_id=subject_id)
            await self._store.mark_derived(subject_id=subject_id, tags_sha256=digest)
            return anomalous
        except Exception:
            log.exception(
                "place/corridor derivation failed for %s — the fold is committed; "
                "the next run derives again", subject_id,
            )
            return []

    async def _place_label(
        self, *, lat: float, lng: float, tag_name: str | None,
        stays: Sequence[Stay], now_ms: int,
    ) -> str | None:
        """The display name for a place, in DEC-190's order: the household's
        own tag name; else a cached Google label; else the Life360 mode
        address of its stays. Each stay carries its own address
        (`presence_stays.address`), so the name is a function of the place's
        stays, not of the cycle that happened to see their fixes."""
        if tag_name is not None:
            return tag_name
        label = await self._label_for(lat=lat, lng=lng)
        if label is not None:
            return label
        return mode_address(s.address for s in stays)

    async def _recheck_labels(
        self, *, subject_id: str, all_stays: list[Stay], now_ms: int,
    ) -> None:
        """The cheap speed: each stored place relabelled from the
        stays linked to it at the logical clock — exactly the label the full
        plan would compute for the same stays and saved places (a saved
        place keeps its tag's name; the plan runs again if the tags change).
        Never moves a place."""
        for place in await self._store.places_for_subject(subject_id=subject_id):
            linked = sorted(
                (s for s in all_stays if s.place_id == place.id),
                key=lambda s: s.started_ts,
            )
            if not linked:
                continue
            recheck = label_place(
                linked, now_ms=now_ms, tz_name=self._cfg.tz_name,
                min_nights=self._cfg.min_nights,
                min_weekdays=self._cfg.min_weekdays,
                label_freshness_days=self._cfg.label_freshness_days,
            )
            name = place.label if place.saved_place_id is not None else await self._place_label(
                lat=place.lat, lng=place.lng, tag_name=None, stays=linked, now_ms=now_ms,
            )
            if (recheck.kind, recheck.confidence, name) == (
                place.kind, place.confidence, place.label,
            ):
                continue
            await self._store.save_place(
                subject_id=subject_id, circle_id=place.circle_id,
                place_id=place.id, lat=place.lat, lng=place.lng,
                radius_m=place.radius_m, kind=recheck.kind,
                confidence=recheck.confidence, label=name,
                saved_place_id=place.saved_place_id,
                first_seen_ts=place.first_seen_ts, last_seen_ts=place.last_seen_ts,
            )
            if (
                self._metrics is not None
                and recheck.kind == "unlabeled" and place.kind != "unlabeled"
            ):
                self._metrics.inc("nora_analytics_unlabeled_total", subject=subject_id)

    async def _reconcile_places(
        self, *, subject_id: str, circle_id: str, all_stays: list[Stay],
        new_stays: list[Stay], tags: list[TaggedPlace], now_ms: int,
    ) -> list[Stay]:
        """DEC-190 (ADR 0014): recompute the
        subject's whole place picture with `plan_places`, then make the store
        match it — save each planned place under the id it keeps, relink
        every stay whose link changed (overwriting, so a link can never
        outlive the place it pointed at), drop places nothing resolves to,
        and rebuild occupancy and rhythm for every place whose stay set
        changed.

        Writes are DIFFED against what is stored, so a steady-state run
        (same stays, same plan) writes nothing. The plan's content is a
        function of the stays, the tags and the clock; only which stored id
        each planned place keeps depends on what is stored (identity by
        overlap), which is why a rebuild's places carry other ids and the
        same everything else."""
        existing = await self._store.places_for_subject(subject_id=subject_id)
        existing_by_id = {p.id: p for p in existing}
        plan = plan_places(
            all_stays, existing=existing, tagged=tags, now_ms=now_ms,
            tz_name=self._cfg.tz_name, cluster_eps_m=self._cfg.cluster_eps_m,
            cluster_min_samples=self._cfg.cluster_min_samples,
            match_radius_m=self._cfg.place_match_radius_m,
            min_nights=self._cfg.min_nights, min_weekdays=self._cfg.min_weekdays,
            label_freshness_days=self._cfg.label_freshness_days,
        )
        links: dict[int, int | None] = {}
        kept: set[int] = set()
        for planned in plan:
            prior = (
                existing_by_id.get(planned.existing_id)
                if planned.existing_id is not None else None
            )
            label = await self._planned_label(planned, now_ms=now_ms)
            first_seen = min(s.started_ts for s in planned.stays)
            last_seen = max(s.ended_ts for s in planned.stays)
            unchanged = prior is not None and (
                prior.lat, prior.lng, prior.radius_m, prior.kind,
                prior.confidence, prior.label, prior.saved_place_id,
                prior.first_seen_ts, prior.last_seen_ts,
            ) == (
                planned.lat, planned.lng, planned.radius_m, planned.label.kind,
                planned.label.confidence, label, planned.saved_place_id,
                first_seen, last_seen,
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
            kept.add(place_id)
            for s in planned.stays:
                links[s.started_ts] = place_id

        changed_links: dict[int, int | None] = {}
        for s in all_stays:
            target = links.get(s.started_ts)
            if target != s.place_id:
                changed_links[s.started_ts] = target
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
        # EVERY kept place, not only the ones whose links changed this run:
        # a run whose occupancy write failed after its relinks landed leaves
        # no link changed for the retry to see, and the retry must still
        # repair it (the writes are diffed, so an unchanged place costs a
        # read, not a version).
        for place_id in sorted(kept):
            await self._rebuild_rhythm(
                subject_id=subject_id, place_id=place_id,
                stays=members.get(place_id, []),
            )
        anomalous: list[Stay] = []
        if new_stays:
            # Anomalies are a pure function of the stays and their links, so
            # they are computed on read (`detect_stay_anomalies`), never
            # stored — a stored copy would go stale on every relink. The
            # rollup COUNTS the ones this run's stays carry, scored against
            # the links it just wrote, and hands them to the deviation
            # stimulus.
            linked = [
                dataclasses.replace(s, place_id=links.get(s.started_ts))
                for s in all_stays
            ]
            by_start = {s.started_ts: s for s in linked}
            for new in new_stays:
                stay = by_start.get(new.started_ts)
                if stay is None:
                    continue
                found = detect_stay_anomalies(
                    stay, history=linked, tz_name=self._cfg.tz_name,
                    min_samples=self._cfg.anomaly_min_samples,
                    z_threshold=self._cfg.anomaly_z_threshold,
                    novelty_radius_m=self._cfg.cluster_eps_m,
                )
                if found:
                    anomalous.append(stay)
                if self._metrics is not None:
                    for anomaly in found:
                        self._metrics.inc(
                            "nora_analytics_anomalies_total",
                            subject=subject_id, kind=anomaly.kind,
                        )
        return anomalous

    async def _planned_label(self, planned: PlannedPlace, *, now_ms: int) -> str | None:
        return await self._place_label(
            lat=planned.lat, lng=planned.lng, tag_name=planned.tag_name,
            stays=planned.stays, now_ms=now_ms,
        )

    async def _rebuild_rhythm(
        self, *, subject_id: str, place_id: int, stays: list[Stay],
    ) -> None:
        """Tier 3 for ONE place, recomputed wholesale
        from the stays linked to it: occupancy (dwell per local hour-of-week)
        and rhythm (circular mean arrival/departure, duration median/MAD).
        Full recompute rather than accumulation, so it is idempotent and
        follows a relink — accumulation would leave dwell on a place after
        its stays had moved. `updated_ts` is the newest stay's end (the
        Record time the row is as of), never a clock reading."""
        as_of = max((s.ended_ts for s in stays), default=0)
        bins: dict[int, int] = {}
        for s in stays:
            for how, dwell in hour_of_week_bins(
                s.started_ts, s.ended_ts, self._cfg.tz_name,
            ).items():
                bins[how] = bins.get(how, 0) + dwell
        await self._store.replace_occupancy(
            subject_id=subject_id, place_id=place_id, bins=bins, now_ms=as_of,
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
            sample_count=len(stays), now_ms=as_of,
        )

    async def _derive_corridors(self, *, subject_id: str) -> None:
        """Tier 5, recomputed from the subject's trips
        as a whole instead of accumulated: a corridor is what the trips add
        up to now, not what the order they arrived in happened to link.

        1. The POOL is the newest `corridor_cluster_batch_limit` trips with a
           road-name sequence (an address-less trip never joins: its empty
           sequence is an edit-distance neighbour of every short corridor).
           `cluster_trips` over it, newest
           first, makes the corridors; `corridor_min_samples` is what
           lets one be born.
        2. A corridor's sequence is the most common sequence among its
           clustered trips (the newest on a tie): a function of its members,
           not of which trip happened to create the row.
        3. Every other named trip — noise inside the pool, and every trip
           older than it — joins the corridor whose sequence is nearest, when
           within `corridor_max_edit_distance` (a second pass over the same
           corridors every time).
        4. Each corridor keeps the stored id whose trips it overlaps most
           (greedy, like places); links, rows and counts are then DIFFED
           against the store.

        Cost: `cluster_trips` is O(n²) in an edit-distance metric; the pool
        bound keeps it bounded, and it runs only when the trips changed."""
        trips = await self._store.trips_for_subject(subject_id=subject_id)
        named = sorted(
            (t for t in trips if t.corridor), key=lambda t: t.started_ts, reverse=True,
        )
        pool = named[: self._cfg.corridor_cluster_batch_limit]
        clusters = [
            list(c) for c in cluster_trips(
                pool, max_edit_distance=self._cfg.corridor_max_edit_distance,
                min_samples=self._cfg.corridor_min_samples,
            )
        ]
        reps = [_representative(c) for c in clusters]
        clustered = {t.started_ts for c in clusters for t in c}
        for trip in named:
            if trip.started_ts in clustered:
                continue
            best: tuple[int, int] | None = None
            for i, rep in enumerate(reps):
                d = edit_distance(trip.corridor, rep)
                if d <= self._cfg.corridor_max_edit_distance and (
                    best is None or (d, i) < best
                ):
                    best = (d, i)
            if best is not None:
                clusters[best[1]].append(trip)

        existing = await self._store.corridors_for_subject(subject_id=subject_id)
        existing_ids = {c.id for c in existing}
        stored_link = {t.started_ts: t.corridor_id for t in trips}
        candidates: list[tuple[int, int, int]] = []
        for ci, members in enumerate(clusters):
            overlap: dict[int, int] = {}
            for t in members:
                cid = stored_link.get(t.started_ts)
                if cid is not None and cid in existing_ids:
                    overlap[cid] = overlap.get(cid, 0) + 1
            candidates.extend((n, ci, cid) for cid, n in overlap.items())
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
        keeps: dict[int, int] = {}
        used: set[int] = set()
        for _n, ci, cid in candidates:
            if ci in keeps or cid in used:
                continue
            keeps[ci] = cid
            used.add(cid)

        by_id = {c.id: c for c in existing}
        links: dict[int, int | None] = {}
        kept: set[int] = set()
        for ci, members in enumerate(clusters):
            starts = [t.started_ts for t in members]
            wanted = (reps[ci], len(members), min(starts), max(starts))
            keep = keeps.get(ci)
            prior = by_id.get(keep) if keep is not None else None
            if prior is not None and (
                prior.sequence, prior.sample_count, prior.first_seen_ts, prior.last_seen_ts,
            ) == wanted:
                cid = prior.id
            else:
                cid = await self._store.save_corridor(
                    subject_id=subject_id, corridor_id=keep, sequence=reps[ci],
                    sample_count=len(members), first_seen_ts=min(starts),
                    last_seen_ts=max(starts),
                )
                if self._metrics is not None:
                    self._metrics.inc(
                        "nora_analytics_corridors_total", subject=subject_id,
                        outcome="created" if prior is None else "reconfirmed",
                    )
            kept.add(cid)
            for t in members:
                links[t.started_ts] = cid
        changed = {
            t.started_ts: links.get(t.started_ts) for t in trips
            if links.get(t.started_ts) != t.corridor_id
        }
        await self._store.relink_trips(subject_id=subject_id, links=changed)
        await self._store.delete_corridors(
            subject_id=subject_id, corridor_ids=sorted(existing_ids - kept),
        )


def _representative(members: Sequence[Trip]) -> tuple[str, ...]:
    """The most common sequence among a corridor's trips; on a tie the one
    seen most recently (members arrive newest first from the pool)."""
    counts: dict[tuple[str, ...], int] = {}
    for t in sorted(members, key=lambda t: t.started_ts, reverse=True):
        counts[t.corridor] = counts.get(t.corridor, 0) + 1
    return max(counts, key=lambda seq: counts[seq])


def _median(ordered: list[float]) -> float:
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _local_minute_of_day(ts_ms: int, tz: ZoneInfo) -> float:
    local = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
    return local.hour * 60.0 + local.minute + local.second / 60.0


def _chunk_by_gap(fixes: list[RawFix], gap_ms: int) -> list[list[RawFix]]:
    """Split ts-ascending `fixes` wherever the gap to the next fix exceeds
    `gap_ms` — see the long comment at this function's one call site for
    why the window needs this before handing it to `segment_stays`."""
    if not fixes:
        return []
    chunks: list[list[RawFix]] = [[fixes[0]]]
    for prev, cur in pairwise(fixes):
        if cur.ts - prev.ts > gap_ms:
            chunks.append([])
        chunks[-1].append(cur)
    return chunks
