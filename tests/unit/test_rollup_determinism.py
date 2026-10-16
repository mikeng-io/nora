"""The gate for the presence Rollup: «incremental and batch builds of each rollup
agree exactly».

One synthetic Record — two people, four days of resting fixes at Life360's
cadence, drives at the moving cadence, a phone that goes quiet at home for 40
minutes, GPS spikes, resting scatter, a reading stored twice (two circles), a
fix uploaded hours late, a named-route commute, saved places, and the
collector's polls with failures and an outage — is folded into one build per
schedule: a tick every 120 s (the daemon), ticks at random intervals, and a
single run straight to the watermark (a rebuild). Every row of every
presence rollup, and where each fold stands, must be identical across the
three. Surrogate ids differ between builds by design; rows are compared by
what they say.

The wall-clock case: an incremental path that decided every tail on the wall
clock it was handed would disagree with a batch that decides on the next fix;
`test_a_quiet_phone_at_home_is_one_stay_on_every_schedule` is that case in
isolation.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from nora.analytics.rollup import MobilityRollup, RollupConfig
from nora.domain.mobility import TaggedPlace
from nora.testing.mobility import FakeMobilityStore, _SeededFix, _SeededPoll

_HKT = ZoneInfo("Asia/Hong_Kong")

CFG = RollupConfig(
    tick_seconds=120, safety_lag=600, settled_seconds=900,
    max_age_seconds=86_400, batch_limit=50, backfill_batch_limit=20_000,
    backfill_threshold=4_000, grace_seconds=60, stay_radius_m=120,
    stay_min_dwell_seconds=600, cluster_eps_m=100, cluster_min_samples=3,
    min_nights=3, min_weekdays=3, label_freshness_days=45,
    corridor_min_samples=2,
)

_START = int(datetime(2026, 9, 14, 0, 0, tzinfo=_HKT).timestamp() * 1000)
_DAYS = 4
_HOME = {"a": (22.4000, 114.1000), "b": (22.3500, 114.2000)}
_WORK = (22.3000, 114.1700)


def _seed_record(store: FakeMobilityStore, *, seed: int = 11, shuffle_ids: bool = False) -> int:
    """Seed the Record synchronously through the fake's lists (the fake's
    seeding coroutines only append), and return the watermark: the end of the
    last day. Rows recorded after it exist too, so a run that read past its
    watermark would show."""
    rng = random.Random(seed)
    t = store._t  # the fake's shared tables
    fid = [1]

    def fix(person: str, ts: int, lat: float, lng: float, *, driving=None,
            address=None, recorded=None, copies=1) -> None:
        rec = ts + rng.randint(0, 20_000) if recorded is None else recorded
        for _ in range(copies):
            t.fixes.append(_SeededFix(
                id=fid[0], person_id=person, ts=ts, lat=lat, lng=lng,
                accuracy_m=50.0, wire_state="live", recorded_at=rec,
                address=address, is_driving=driving,
            ))
            fid[0] += 1

    def rest(person: str, start: int, end: int, at: tuple[float, float], *,
             address: str, quiet: tuple[int, int] | None = None) -> None:
        ts = start
        while ts < end:
            if quiet is None or not (quiet[0] <= ts < quiet[1]):
                jitter = rng.choice([0.0, 0.0002, 0.0005])
                roll = rng.random()
                if roll < 0.04:  # a spike well outside the radius, then back
                    fix(person, ts, at[0] + 0.004, at[1] + 0.004, driving=False, address=address)
                elif roll < 0.06:  # the same reading stored once per circle
                    fix(person, ts, at[0], at[1], driving=False, address=address, copies=2)
                else:
                    fix(person, ts, at[0] + rng.uniform(-jitter, jitter),
                        at[1] + rng.uniform(-jitter, jitter),
                        driving=rng.choice([None, False, False, True]), address=address)
            ts += rng.choice([300_000, 600_000, 900_000, 1_500_000])

    def drive(person: str, start: int, a: tuple[float, float], b: tuple[float, float],
              roads: list[str]) -> int:
        steps = len(roads)
        for i, road in enumerate(roads):
            frac = i / (steps - 1)
            fix(person, start + i * 25_000, a[0] + (b[0] - a[0]) * frac,
                a[1] + (b[1] - a[1]) * frac, driving=True, address=road)
        return start + steps * 25_000

    for day in range(_DAYS):
        d0 = _START + day * 86_400_000
        for person in ("a", "b"):
            home = _HOME[person]
            quiet_at = d0 + 3 * 3_600_000
            quiet = (quiet_at, quiet_at + 40 * 60_000) if person == "a" else None
            rest(person, d0, d0 + 8 * 3_600_000, home, address=f"{person} Home Road", quiet=quiet)
            roads = [f"{person} Road {i}" for i in range(20)]
            arrive = drive(person, d0 + 8 * 3_600_000, home, _WORK, roads)
            rest(person, arrive + 60_000, d0 + 18 * 3_600_000, _WORK, address="Office Tower")
            back = drive(person, d0 + 18 * 3_600_000, _WORK, home, list(reversed(roads)))
            rest(person, back + 60_000, d0 + 24 * 3_600_000, home, address=f"{person} Home Road")
    # A fix uploaded hours late: its position is on day 1, its row on day 2.
    fix("a", _START + 86_400_000 + 12 * 3_600_000, 22.3001, 114.1701,
        driving=False, recorded=_START + 2 * 86_400_000 + 3_600_000)
    watermark = _START + _DAYS * 86_400_000
    # The collector's polls: every 90-300 s, a few failures, one 2-hour outage.
    at = _START
    pid = 1
    while at < watermark + 3_600_000:
        outcome = "ok"
        if _START + 86_400_000 + 5 * 3_600_000 <= at < _START + 86_400_000 + 7 * 3_600_000:
            outcome = "challenge"
        elif rng.random() < 0.02:
            outcome = "error"
        t.polls.append(_SeededPoll(
            id=pid, scope="c1", outcome=outcome, at_ms=at,
            recorded_ms=at + rng.randint(0, 3_000),
        ))
        pid += 1
        at += rng.choice([90_000, 120_000, 300_000])
    t.tagged.append(TaggedPlace("c1", "tag-home-a", "Home A", *_HOME["a"], 80.0))
    if shuffle_ids:
        # A row's id says when it was WRITTEN, which need not follow the time
        # it describes (a late upload, a backfill, two writers). The fold
        # orders by the Record's clock and must not lean on ids beyond a
        # tie-break: give the fixes ids in no particular order.
        ids = [f.id for f in t.fixes]
        rng.shuffle(ids)
        for f, new_id in zip(t.fixes, ids, strict=True):
            f.id = new_id
    return watermark


async def _snapshot(store: FakeMobilityStore, *, subjects=("a", "b")) -> dict:
    """Everything a build holds for the subjects, by what it says: a place is
    named by its content and its stays by the place they are linked to, so
    two builds with different surrogate ids compare equal when they agree."""
    out: dict = {}
    for subject in subjects:
        places = await store.places_for_subject(subject_id=subject)
        name = {
            p.id: (round(p.lat, 9), round(p.lng, 9), round(p.radius_m, 6), p.kind,
                   round(p.confidence, 9), p.label, p.saved_place_id,
                   p.first_seen_ts, p.last_seen_ts)
            for p in places
        }
        corridors = await store.corridors_for_subject(subject_id=subject)
        route = {
            c.id: (c.sequence, c.sample_count, c.first_seen_ts, c.last_seen_ts)
            for c in corridors
        }
        stays = [
            dataclasses.replace(s, place_id=None, lat=round(s.lat, 9), lng=round(s.lng, 9),
                                radius_m=round(s.radius_m, 6))
            for s in await store.stays_for_subject(subject_id=subject)
        ]
        links = [name.get(s.place_id) for s in await store.stays_for_subject(subject_id=subject)]
        trips = [
            (dataclasses.replace(t, corridor_id=None, distance_m=round(t.distance_m, 6),
                                 avg_speed_kmh=round(t.avg_speed_kmh, 6)),
             route.get(t.corridor_id))
            for t in await store.trips_for_subject(subject_id=subject)
        ]
        out[subject] = {
            "stays": stays, "links": links, "trips": trips,
            "places": sorted(name.values(), key=repr),
            "corridors": sorted(route.values()),
            "occupancy": sorted(
                (name[p.id], sorted(store.occupancy_for(subject_id=subject, place_id=p.id).items()))
                for p in places
            ),
            "rhythm": sorted(
                (name[p.id], sorted(
                    (store.rhythm_for(subject_id=subject, place_id=p.id) or {}).items()
                ))
                for p in places
            ),
            "fold": await store.fold_state(subject_id=subject),
        }
    return out


async def _fold(store: FakeMobilityStore, schedule: list[int]) -> None:
    rollup = MobilityRollup(store=store, config=CFG)
    for through in schedule:
        for subject in ("a", "b"):
            await rollup.run_once(
                subject_id=subject, person_ids=[subject], circle_id="c1",
                through_ms=through,
            )


def _schedules(watermark: int, seed: int) -> dict[str, list[int]]:
    rng = random.Random(seed)
    every_tick = [*range(_START, watermark, 120_000), watermark]
    ragged: list[int] = []
    at = _START
    while at < watermark:
        at += rng.choice([30_000, 45_000, 300_000, 900_000, 3_600_000, 7_200_000])
        ragged.append(min(at, watermark))
    return {"every_tick": every_tick, "ragged": ragged, "rebuild": [watermark]}


@pytest.mark.parametrize(("seed", "shuffle_ids"), [(11, False), (23, False), (5, True)])
async def test_every_schedule_folds_the_record_into_the_same_rollup(seed, shuffle_ids):
    tables = FakeMobilityStore()
    watermark = _seed_record(tables, seed=seed, shuffle_ids=shuffle_ids)
    snapshots = {}
    for build, (name, schedule) in enumerate(_schedules(watermark, seed).items(), start=1):
        store = tables.scoped(build)
        await _fold(store, schedule)
        snapshots[name] = await _snapshot(store)
    rebuild = snapshots["rebuild"]
    # Positive controls: the Record really produced every kind of row, so
    # equality is not two empty builds agreeing.
    for subject in ("a", "b"):
        assert len(rebuild[subject]["stays"]) >= 2 * _DAYS
        assert len(rebuild[subject]["trips"]) >= 2 * (_DAYS - 1)
        assert rebuild[subject]["places"] and rebuild[subject]["occupancy"]
        assert rebuild[subject]["corridors"]
    # Agreement is not correctness — a defect in the fold is on every
    # schedule alike — so the rows must also be sane: one person is in one
    # stay or one trip at a time, never two.
    for subject in ("a", "b"):
        spans = sorted(
            [(s.started_ts, s.ended_ts) for s in rebuild[subject]["stays"]]
            + [(t.started_ts, t.ended_ts) for t, _ in rebuild[subject]["trips"]]
        )
        assert all(a[1] < b[0] for a, b in pairwise(spans)), subject
    assert rebuild["a"]["fold"].late_fixes == 1  # the straggler was seen and dropped
    assert any(p[6] == "tag-home-a" for p in rebuild["a"]["places"])
    for name in ("every_tick", "ragged"):
        for subject in ("a", "b"):
            for part, value in rebuild[subject].items():
                assert snapshots[name][subject][part] == value, (name, subject, part)


async def test_a_quiet_phone_at_home_is_one_stay_on_every_schedule():
    """The wall-clock case, alone: one fix at home, then 40 minutes with no
    fix at all, then fixes at home again. A path that closed the stay on the
    wall clock after `DWELL_CAP_MS` would open a second; a batch
    re-derive sees the next fix and keeps one. On the fold, the close needs a
    poll proving the time passed, so the answer depends on the Record only:
    with no poll in the silence it is one stay on every schedule, and with
    polls in the silence it is two on every schedule."""
    for polled in (False, True):
        results = []
        for schedule in ("each", "once"):
            store = FakeMobilityStore()
            base = _START
            times = [0, 10, 20, 60, 70, 80]  # minutes: 40 quiet after minute 20
            for i, minute in enumerate(times):
                await store._seed_fix_for_test(
                    id=i + 1, person_id="a", ts=base + minute * 60_000,
                    lat=22.4, lng=114.1, accuracy_m=50.0, wire_state="live",
                    recorded_at=base + minute * 60_000, address="a Home Road",
                )
            await store._seed_fix_for_test(
                id=99, person_id="a", ts=base + 200 * 60_000, lat=22.5, lng=114.3,
                accuracy_m=50.0, wire_state="live", recorded_at=base + 200 * 60_000,
            )
            await store._seed_fix_for_test(
                id=100, person_id="a", ts=base + 201 * 60_000, lat=22.5, lng=114.3,
                accuracy_m=50.0, wire_state="live", recorded_at=base + 201 * 60_000,
            )
            if polled:
                for minute in range(21, 60, 2):
                    await store._seed_poll_for_test(at_ms=base + minute * 60_000)
            rollup = MobilityRollup(store=store, config=CFG)
            ticks = (
                [base + m * 60_000 for m in range(0, 202)] if schedule == "each"
                else [base + 202 * 60_000]
            )
            for through in ticks:
                await rollup.run_once(
                    subject_id="a", person_ids=["a"], circle_id="c1", through_ms=through,
                )
            results.append([
                (s.started_ts, s.ended_ts) for s in await store.stays_for_subject(subject_id="a")
            ])
        assert results[0] == results[1]
        assert len(results[0]) == (2 if polled else 1)
