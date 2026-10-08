"""Behaviours of the presence Rollup fold that the schedule-agreement gate
(`test_rollup_determinism.py`) cannot see, on SYNTHETIC data only.

People are `p_alpha`; places are invented points around a neutral spot. Each
test here is named in `scripts/mutations/cognition-step2a-rollup.json` with
the one-line change that must turn it red.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nora.analytics.rollup import MobilityRollup, RollupConfig
from nora.testing.mobility import FakeMobilityStore

_HKT = ZoneInfo("Asia/Hong_Kong")
_MIN = 60_000
_HOUR = 60 * _MIN
_DAY = 24 * _HOUR

CFG = RollupConfig(
    tick_seconds=120, safety_lag=600, settled_seconds=900,
    max_age_seconds=86_400, batch_limit=50, backfill_batch_limit=20_000,
    backfill_threshold=4_000, grace_seconds=60, stay_radius_m=120,
    stay_min_dwell_seconds=600, cluster_eps_m=100, cluster_min_samples=3,
    min_nights=3, min_weekdays=3, label_freshness_days=45,
    corridor_min_samples=2,
)

PLACE_A = (22.3000, 114.1700)
PLACE_B = (22.3300, 114.2000)
SUBJECT = "p_alpha"


class _Record:
    """Seeds a synthetic Record into the fake store, one fix at a time."""

    def __init__(self) -> None:
        self.store = FakeMobilityStore()
        self._next_id = 1

    async def fix(self, ts: int, at: tuple[float, float], *, driving: bool | None = None) -> None:
        await self.store._seed_fix_for_test(
            id=self._next_id, person_id=SUBJECT, ts=ts, lat=at[0], lng=at[1],
            accuracy_m=50.0, wire_state="live", recorded_at=ts + 1_000,
            address=None, is_driving=driving,
        )
        self._next_id += 1

    async def rest(self, start: int, end: int, at: tuple[float, float], *, step: int = 10 * _MIN):
        for ts in range(start, end + 1, step):
            await self.fix(ts, at, driving=False)

    async def fold(self, through_ms: int) -> None:
        await MobilityRollup(store=self.store, config=CFG).run_once(
            subject_id=SUBJECT, person_ids=[SUBJECT], circle_id="c_one", through_ms=through_ms,
        )


async def test_a_stale_home_label_flips_to_unlabeled_on_a_recheck_with_zero_new_fixes():
    # Four weekday nights at Place A (22:00 - 06:30) and days at Place B: A is
    # the home. Then nothing new is recorded but a successful poll, long after
    # the label's freshness window: no stay or trip changed, so only the
    # label re-check can notice the support went stale.
    record = _Record()
    monday = int(datetime(2026, 9, 14, 0, 0, tzinfo=_HKT).timestamp() * 1000)
    for day in range(4):
        d0 = monday + day * _DAY
        await record.rest(d0, d0 + 6 * _HOUR + 30 * _MIN, PLACE_A)
        await record.rest(d0 + 9 * _HOUR, d0 + 17 * _HOUR, PLACE_B)
        await record.rest(d0 + 22 * _HOUR, d0 + 23 * _HOUR + 50 * _MIN, PLACE_A)
    end = monday + 4 * _DAY
    # One lone fix far away ends the last night at Place A; a single fix never
    # dwells long enough to be a stay itself. A poll two hours on proves the
    # time passed, so every stay is written before the stale poll below.
    await record.fix(end + 5 * _MIN, (22.4500, 114.3500), driving=True)
    await record.store._seed_poll_for_test(
        at_ms=end + 2 * _HOUR, recorded_ms=end + 2 * _HOUR, scope="c_one",
    )
    await record.fold(end + 3 * _HOUR)
    kinds = {p.kind for p in await record.store.places_for_subject(subject_id=SUBJECT)}
    assert "home" in kinds, kinds

    stays_before = await record.store.stays_for_subject(subject_id=SUBJECT)
    await record.store._seed_poll_for_test(
        at_ms=end + 90 * _DAY, recorded_ms=end + 90 * _DAY, scope="c_one",
    )
    await record.fold(end + 91 * _DAY)

    # The re-check path, not a re-plan: the stays are exactly as they were.
    assert await record.store.stays_for_subject(subject_id=SUBJECT) == stays_before
    kinds = {p.kind for p in await record.store.places_for_subject(subject_id=SUBJECT)}
    assert "home" not in kinds, kinds


async def test_a_two_fix_drive_is_recorded_as_a_trip():
    # A rest at Place A, a sparse drive of exactly two fixes 30 minutes apart
    # (the phone reports "not driving" on both), then an arrival at Place B.
    # The fold evaluates after every fix: at the first arrival fix the stop is
    # provisional and the movement before it is still too short to be a trip,
    # so those fixes must be HELD, not discarded as noise, or the drive is
    # never recorded once the arrival confirms.
    record = _Record()
    t0 = int(datetime(2026, 9, 14, 8, 0, tzinfo=_HKT).timestamp() * 1000)
    await record.rest(t0, t0 + 60 * _MIN, PLACE_A)
    third = tuple(a + (b - a) / 3 for a, b in zip(PLACE_A, PLACE_B, strict=True))
    two_thirds = tuple(a + 2 * (b - a) / 3 for a, b in zip(PLACE_A, PLACE_B, strict=True))
    await record.fix(t0 + 70 * _MIN, third, driving=False)
    await record.fix(t0 + 100 * _MIN, two_thirds, driving=False)
    await record.rest(t0 + 125 * _MIN, t0 + 155 * _MIN, PLACE_B)
    await record.fold(t0 + 10 * _HOUR)

    trips = await record.store.trips_for_subject(subject_id=SUBJECT)
    assert [(t.started_ts, t.ended_ts) for t in trips] == [(t0 + 70 * _MIN, t0 + 100 * _MIN)]
    stays = await record.store.stays_for_subject(subject_id=SUBJECT)
    assert (stays[0].started_ts, stays[0].ended_ts) == (t0, t0 + 60 * _MIN)
