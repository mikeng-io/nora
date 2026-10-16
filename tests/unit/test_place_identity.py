"""DEC-190 — place identity (ADR 0014).

The regressions ADR 0014's Consequences section says the build owes, each
against the defect it names: a single `(subject, kind)` slot that let a second
`home` cluster overwrite the first one's coordinates while the first kept the
links (stays linked kilometres from where they happened), `unlabeled`
stored as a deletion, and a Speed-2 recheck that wrote a flipped place at the
subject's chronologically first stay.

Pure `plan_places` tests first, then the rollup driving the fake store.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nora.analytics.rollup import MobilityRollup, RollupConfig
from nora.domain.mobility import (
    DerivedPlace,
    Stay,
    TaggedPlace,
    dwell_weighted_centroid,
    plan_places,
)
from nora.domain.presence import haversine_distance_m
from nora.testing.mobility import FakeMobilityStore

_HKT = ZoneInfo("Asia/Hong_Kong")
_DAY = 86_400_000
# Midnight HKT on a Tuesday.
_T0 = int(datetime(2026, 9, 1, tzinfo=_HKT).timestamp() * 1000)
NOW = _T0 + 30 * _DAY

HOME_A = (22.3882, 114.0371)   # primary residence
HOME_B = (22.3512, 114.0632)   # second residence, ~5 km away
OFFICE = (22.3756, 114.1973)

CFG = RollupConfig(
    tick_seconds=120, safety_lag=600, settled_seconds=900,
    max_age_seconds=86_400, batch_limit=400, backfill_batch_limit=20_000,
    backfill_threshold=4_000, grace_seconds=60, stay_radius_m=120,
    stay_min_dwell_seconds=600, cluster_eps_m=100, cluster_min_samples=3,
    min_nights=3, min_weekdays=3, label_freshness_days=45,
)


def _stay(day: int, start_h: float, end_h: float, at: tuple[float, float], *,
          place_id: int | None = None, jitter_m: float = 0.0) -> Stay:
    base = datetime(2026, 9, 1, tzinfo=_HKT) + timedelta(days=day)
    start = int((base + timedelta(hours=start_h)).timestamp() * 1000)
    end = int((base + timedelta(hours=end_h)).timestamp() * 1000)
    d_lat = jitter_m / 111_320.0
    return Stay(
        subject_id="s", person_id="s", circle_id="c1", started_ts=start,
        ended_ts=end, lat=at[0] + d_lat, lng=at[1], radius_m=30.0,
        fix_count=10, place_id=place_id,
    )


def _nights(at: tuple[float, float], days: range, **kw) -> list[Stay]:
    # 22:00 -> 07:00 next morning: squarely home-shaped.
    return [_stay(d, 22, 31, at, jitter_m=(d % 3) * 10, **kw) for d in days]


def _plan(stays, *, existing=(), tagged=(), match_radius_m=150.0):
    return plan_places(
        list(stays), existing=list(existing), tagged=list(tagged), now_ms=NOW,
        tz_name="Asia/Hong_Kong", cluster_eps_m=100.0, cluster_min_samples=3,
        match_radius_m=match_radius_m, min_nights=3, min_weekdays=3,
        label_freshness_days=45,
    )


def _row(pid: int, at: tuple[float, float], kind: str = "home", **kw) -> DerivedPlace:
    return DerivedPlace(
        id=pid, subject_id="s", circle_id="c1", lat=at[0], lng=at[1],
        radius_m=100.0, kind=kind, confidence=1.0, label=kw.get("label"),  # type: ignore[arg-type]
        saved_place_id=kw.get("saved_place_id"),
    )


# --- plan_places -----------------------------------------------------------


def test_a_subject_with_two_residences_gets_two_home_places():
    # Regression 5, and the ceiling itself: two clusters that both label
    # `home` are two places, not a fight over one slot.
    stays = _nights(HOME_A, range(0, 20, 2)) + _nights(HOME_B, range(1, 20, 2))
    plan = _plan(stays)
    assert sorted(p.label.kind for p in plan) == ["home", "home"]
    for p in plan:
        assert all(
            haversine_distance_m(p.lat, p.lng, s.lat, s.lng) <= 100 for s in p.stays
        )


def test_the_corrupted_row_keeps_its_links_and_the_displaced_cluster_gets_a_new_id():
    # Regression 1. Row 7 holds HOME_B's coordinates
    # (the later cluster overwrote them) but HOME_A's links (the first
    # cluster's backfill). Identity must follow the LINKS: HOME_A keeps id 7
    # and HOME_B gets a fresh row, so no stay ends up pointing 5 km away.
    stays = (
        _nights(HOME_A, range(0, 20, 2), place_id=7)
        + _nights(HOME_B, range(1, 20, 2))
    )
    plan = _plan(stays, existing=[_row(7, HOME_B)])
    by_id = {p.existing_id: p for p in plan}
    assert set(by_id) == {7, None}
    assert haversine_distance_m(by_id[7].lat, by_id[7].lng, *HOME_A) < 30
    assert haversine_distance_m(by_id[None].lat, by_id[None].lng, *HOME_B) < 30


def test_a_planned_place_takes_its_geometry_from_its_stays_never_the_pin():
    # Regression 3 and rule 1: a tag 90 m from where the stays actually are
    # names the place; the coordinates stay the stays' own.
    pin = (HOME_A[0] + 90 / 111_320.0, HOME_A[1])
    tag = TaggedPlace("c1", "tag-home", "Carver Home", pin[0], pin[1], 50.0)
    stays = _nights(HOME_A, range(5))
    [place] = _plan(stays, tagged=[tag])
    assert place.saved_place_id == "tag-home"
    assert place.tag_name == "Carver Home"
    lat, lng = dwell_weighted_centroid(stays)
    assert (place.lat, place.lng) == (lat, lng)


def test_one_stay_at_a_tagged_place_is_already_a_place():
    # Rule 2: no clustering, no min_nights gate for a tagged place — and a
    # single stay is `unlabeled`, stored as such.
    tag = TaggedPlace("c1", "gym", "Gym", OFFICE[0], OFFICE[1], 80.0)
    [place] = _plan([_stay(3, 19, 20.5, OFFICE)], tagged=[tag])
    assert place.saved_place_id == "gym"
    assert place.label.kind == "unlabeled"
    # ...where the same lone stay with no tag is DBSCAN noise.
    assert _plan([_stay(3, 19, 20.5, OFFICE)]) == []


def test_the_match_radius_is_not_the_cluster_radius():
    # A cluster whose centroid sits 130 m from a tag: past cluster_eps_m
    # (100 m) and past the pin's own 50 m radius, inside match_radius_m
    # (150 m). Rule 3 adopts it; at a 100 m match radius it is its own place.
    pin = (HOME_A[0] + 130 / 111_320.0, HOME_A[1])
    tag = TaggedPlace("c1", "t", "Home", pin[0], pin[1], 50.0)
    stays = _nights(HOME_A, range(5))
    [adopted] = _plan(stays, tagged=[tag], match_radius_m=150.0)
    assert adopted.saved_place_id == "t"
    [own] = _plan(stays, tagged=[tag], match_radius_m=100.0)
    assert own.saved_place_id is None


def test_duplicate_tags_in_two_circles_merge_into_one_place():
    # Regression 6: two tags ~50 m apart in different circles are one residence.
    north = (HOME_A[0] + 25 / 111_320.0, HOME_A[1])
    south = (HOME_A[0] - 25 / 111_320.0, HOME_A[1])
    tags = [
        TaggedPlace("c2", "b", "Home (circle 2)", south[0], south[1], 60.0),
        TaggedPlace("c1", "a", "Home", north[0], north[1], 60.0),
    ]
    stays = _nights(north, range(0, 6, 2)) + _nights(south, range(1, 6, 2))
    [place] = _plan(stays, tagged=tags)
    assert (place.saved_place_id, place.tag_name) == ("a", "Home")
    assert len(place.stays) == 6


def test_a_subject_with_no_tagged_place_is_still_auto_identified():
    # Regression 7: derivation is outranked,
    # never removed.
    [place] = _plan(_nights(HOME_A, range(5)))
    assert place.saved_place_id is None
    assert place.label.kind == "home"


def test_an_unlabelled_cluster_is_planned_not_dropped():
    # Regression 4: three weekend afternoons somewhere — a place, `unlabeled`.
    stays = [_stay(d, 14, 16, OFFICE) for d in (4, 5, 11)]  # Sat, Sun, Sat
    [place] = _plan(stays)
    assert place.label.kind == "unlabeled"


def test_identity_is_stable_across_replans():
    # Regression 2: the same stays, re-planned against what the first plan
    # stored, keep every id.
    stays = _nights(HOME_A, range(0, 20, 2)) + _nights(HOME_B, range(1, 20, 2))
    first = _plan(stays)
    rows, linked = [], []
    for pid, p in enumerate(first, start=1):
        rows.append(_row(pid, (p.lat, p.lng)))
        linked += [dataclasses.replace(s, place_id=pid) for s in p.stays]
    second = _plan(linked, existing=rows)
    assert sorted(p.existing_id for p in second) == [1, 2]


# --- the rollup, end to end -------------------------------------------------


async def _seed_nights(store, at, days, fid, *, hour=1):
    for d in days:
        base = int(
            (datetime(2026, 9, 1, tzinfo=_HKT) + timedelta(days=d, hours=hour))
            .timestamp() * 1000
        )
        for i in range(4):
            await store._seed_fix_for_test(
                id=fid, person_id="s", ts=base + i * 600_000, lat=at[0], lng=at[1],
                accuracy_m=40.0, wire_state="live", recorded_at=base + i * 600_000,
            )
            fid += 1
    return fid


async def _tick(rollup, store, now_ms):
    """A daemon tick at `now_ms`: the collector's poll is on the Record (the
    fold's evidence that the time passed), then the fold runs to it."""
    await store._seed_poll_for_test(at_ms=now_ms)
    await rollup.run_once(subject_id="s", person_ids=["s"], circle_id="c1", through_ms=now_ms)


async def test_the_rollup_links_every_stay_to_its_own_residence():
    store = FakeMobilityStore()
    fid = await _seed_nights(store, HOME_A, range(0, 12, 2), 1)
    await _seed_nights(store, HOME_B, range(1, 12, 2), fid)
    rollup = MobilityRollup(store=store, config=CFG)
    await _tick(rollup, store, NOW)
    places = {p.id: p for p in await store.places_for_subject(subject_id="s")}
    assert sorted(p.kind for p in places.values()) == ["home", "home"]
    stays = await store.stays_for_subject(subject_id="s")
    assert len(stays) == 12
    for s in stays:
        place = places[s.place_id]
        assert haversine_distance_m(s.lat, s.lng, place.lat, place.lng) <= place.radius_m


async def test_the_rollup_repairs_a_mislinked_stay_set():
    # Regression 1 through the real loop: seed the exact corrupted state the
    # single-slot writer left behind, then run one cycle.
    store = FakeMobilityStore()
    fid = await _seed_nights(store, HOME_A, range(0, 12, 2), 1)
    await _seed_nights(store, HOME_B, range(1, 12, 2), fid)
    rollup = MobilityRollup(store=store, config=CFG)
    await _tick(rollup, store, NOW)
    # Corrupt it: collapse to ONE row at HOME_B holding HOME_A's links, and
    # HOME_B's stays linked to it too (the 12.1 km case).
    stays = await store.stays_for_subject(subject_id="s")
    rows = await store.places_for_subject(subject_id="s")
    keep = min(p.id for p in rows)
    await store.delete_places(subject_id="s", place_ids=[p.id for p in rows if p.id != keep])
    await store.save_place(
        subject_id="s", circle_id="c1", place_id=keep, lat=HOME_B[0], lng=HOME_B[1],
        radius_m=100.0, kind="home", confidence=0.05, label=None,
        saved_place_id=None, first_seen_ts=0, last_seen_ts=0,
    )
    await store.relink_stays(subject_id="s", links={s.started_ts: keep for s in stays})
    # A re-plan repairs it: the fold clears its plan key whenever stays,
    # trips or saved places change; here that is forced, and one more event
    # is on the Record for the run to consume.
    await store.mark_derived(subject_id="s", tags_sha256="stale")
    await store._seed_fix_for_test(
        id=10_000, person_id="s", ts=NOW - 3_600_000, lat=HOME_A[0], lng=HOME_A[1],
        accuracy_m=40.0, wire_state="live", recorded_at=NOW + 1,
    )
    await _tick(rollup, store, NOW + 60_000)
    places = {p.id: p for p in await store.places_for_subject(subject_id="s")}
    assert len(places) == 2
    for s in await store.stays_for_subject(subject_id="s"):
        place = places[s.place_id]
        assert haversine_distance_m(s.lat, s.lng, place.lat, place.lng) <= place.radius_m
    # Occupancy followed the relink: no hour-of-week dwell is left on a place
    # none of whose stays covers that hour, and the two places split it.
    per_place = {
        pid: sum(store.occupancy_for(subject_id="s", place_id=pid).values())
        for pid in places
    }
    assert all(v > 0 for v in per_place.values())
    assert set(store._t.occupancy) == {
        k for k in store._t.occupancy if k[2] in places
    }  # no bin left on a deleted place


async def test_a_tagged_place_names_the_derived_place_and_keeps_its_id():
    store = FakeMobilityStore()
    await store._seed_tagged_place_for_test(
        TaggedPlace("c1", "l360-home", "Fung & Clara Home", HOME_A[0], HOME_A[1], 80.0)
    )
    await _seed_nights(store, HOME_A, range(0, 8), 1)
    rollup = MobilityRollup(store=store, config=CFG)
    await _tick(rollup, store, NOW)
    [place] = await store.places_for_subject(subject_id="s")
    assert (place.label, place.saved_place_id, place.kind) == (
        "Fung & Clara Home", "l360-home", "home",
    )


async def test_the_label_recheck_never_moves_a_place():
    # Regression 8: a place whose label goes stale keeps its OWN coordinates;
    # the old recheck wrote the subject's first-ever stay's lat/lng onto it.
    store = FakeMobilityStore()
    fid = await _seed_nights(store, OFFICE, [0], 1, hour=14)   # first-ever stay, elsewhere
    await _seed_nights(store, HOME_A, range(1, 8), fid)
    rollup = MobilityRollup(store=store, config=CFG)
    await _tick(rollup, store, NOW)
    home = next(p for p in await store.places_for_subject(subject_id="s") if p.kind == "home")
    await _tick(rollup, store, NOW + 90 * _DAY)
    after = next(p for p in await store.places_for_subject(subject_id="s") if p.id == home.id)
    assert after.kind == "unlabeled"
    assert (after.lat, after.lng) == (home.lat, home.lng)


async def test_a_steady_state_cycle_writes_no_place():
    store = FakeMobilityStore()
    await _seed_nights(store, HOME_A, range(0, 8), 1)
    rollup = MobilityRollup(store=store, config=CFG)
    await _tick(rollup, store, NOW)
    writes: list[int | None] = []
    original = store.save_place

    async def counting(**kw):  # type: ignore[no-untyped-def]
        writes.append(kw["place_id"])
        return await original(**kw)

    store.save_place = counting  # type: ignore[method-assign]
    stays = await store.stays_for_subject(subject_id="s")
    await rollup._reconcile_places(
        subject_id="s", circle_id="c1", all_stays=stays, new_stays=[], tags=[],
        now_ms=NOW,
    )
    assert writes == []


async def test_last_seen_follows_the_stays_even_when_nothing_else_changes():
    # The steady-state diff includes first/last seen: a place whose geometry
    # and label never move must still advance its last_seen as its stays run on.
    store = FakeMobilityStore()
    rollup = MobilityRollup(store=store, config=CFG)
    fid = 1
    for day in range(6):
        fid = await _seed_nights(store, OFFICE, [day], fid, hour=19)
        await _tick(rollup, store, _T0 + (day + 1) * _DAY,
        )
    [place] = await store.places_for_subject(subject_id="s")
    stays = await store.stays_for_subject(subject_id="s")
    assert place.last_seen_ts == max(s.ended_ts for s in stays if s.place_id == place.id)
    assert place.first_seen_ts == min(s.started_ts for s in stays if s.place_id == place.id)
