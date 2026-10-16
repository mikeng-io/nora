"""The load-bearing test: replays a SYNTHETIC fixture engineered to reproduce
six qualitative scenarios of a live `presence_fixes` corpus, and asserts the
derived output against them. Reverting the accuracy gate or the dwell cap must
turn this red — a fixture that stays green either way is decorative
(CLAUDE.md).

The fixture (`tests/fixtures/mobility_scenarios.py`) is synthetic,
programmatically generated data rather than real people's location history;
see `mobility_scenarios.py`'s own docstring for the generator design and the
numbers.

`nora.domain.mobility.label_place` labels a cluster home above
`_HOME_NIGHT_SHARE_MIN = 0.3` (comfortably above the ambiguous case's share,
~0.235, while making realistic overnight patterns up to ~13.3h reachable), and
work only with `_MIN_WORK_DWELL_MS` (8h) of dwell. The `synthetic_home_only_c`
and `synthetic_renamed_*` assertions pass under those thresholds.

`synthetic_wfh_b`'s assertion is `xfail(strict=True)`. No single `night_share`
threshold can accept a work-from-home pattern while still rejecting the
substantial-dwell-in-both-windows ambiguous case: the ambiguous case's share is
higher at every night-window denominator width from 4h (the bare night window)
to 24h (all hours), so any threshold that admits the former also admits the
latter. This fixture's `synthetic_wfh_b` (night_share ≈ 0.1818) and
`synthetic_ambiguous_e` (night_share ≈ 0.2353) reproduce that near-miss
relationship (see `mobility_scenarios.py`'s docstring for the numeric
derivation) — keeping the ambiguous case unlabeled is the non-negotiable
regression (fail-closed labelling's central proof case), so this assertion is
`xfail` rather than curve-fit around it. `strict=True` means an XPASS (this
assertion starts passing on some future change) fails the suite, forcing
someone to notice and remove the marker — a tripwire, not a permanent excuse.
"""
from __future__ import annotations

import pytest

from nora.domain.mobility import RawFix, cluster_places, label_place, segment_stays
from nora.domain.presence import haversine_distance_m
from tests.fixtures.mobility_scenarios import CORPUS_ROWS, CORPUS_ROWS_UNFILTERED


def _fixes_for(person_id: str) -> list[RawFix]:
    rows = [r for r in CORPUS_ROWS if r[0] == person_id and r[5] == "live"]
    rows.sort(key=lambda r: r[1])
    return [RawFix(id=i, ts=r[1], lat=r[2], lng=r[3]) for i, r in enumerate(rows)]


def _derive(person_id: str, *, now_ms: int):
    fixes = _fixes_for(person_id)
    result = segment_stays(
        fixes, subject_id=person_id, person_id=person_id, circle_id="c1",
        radius_m=120.0, min_dwell_ms=600_000,
    )
    stays = list(result.closed)
    if result.open_stay is not None:
        stays.append(result.open_stay)  # treat the trailing group as closed for this replay
    clusters = cluster_places(stays, eps_m=100.0, min_samples=3)
    labels = [
        (cluster, label_place(
            cluster, now_ms=now_ms, tz_name="Asia/Hong_Kong",
            min_nights=3, min_weekdays=3, label_freshness_days=45,
        ))
        for cluster in clusters
    ]
    return labels


# 19 Aug 2026 00:00 UTC — past every fix in the fixture (which runs 3 through
# 14 Aug 2026 HKT). `now_ms` must come after every fix: before them, a
# negative "age" is never greater than a positive freshness window, so
# `label_place`'s `now_ms - support > freshness_ms` staleness check never fires
# and the freshness gate is silently defeated rather than exercised.
NOW_MS = 1_787_097_600_000


def test_synthetic_worker_a_yields_a_work_place_with_zero_night_dwell():
    # The clean-office-worker shape: 09:00-18:00 weekdays, zero night dwell at
    # that cell — the cluster's own is_work gate (night_share < 0.1) is what
    # lets it resolve to "work" at all.
    labels = _derive("synthetic_worker_a", now_ms=NOW_MS)
    work = [(c, r) for c, r in labels if r.kind == "work"]
    assert len(work) == 1, "synthetic_worker_a should yield exactly one work cluster"
    cluster, result = work[0]
    lat = sum(s.lat for s in cluster) / len(cluster)
    lng = sum(s.lng for s in cluster) / len(cluster)
    assert abs(lat - 22.301) < 0.01
    assert abs(lng - 114.155) < 0.01
    assert result.confidence > 0.0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Task 13b lowered is_home's night_share floor from 0.5 to 0.3 "
        "(_HOME_NIGHT_SHARE_MIN), but a genuine work-from-home pattern -- "
        "substantial dwell at ONE location across both the night window "
        "and the working window, because the subject works from where "
        "she sleeps -- still lands below it (this fixture's "
        "synthetic_wfh_b: 132h total dwell at her home cell, only 24h of "
        "it in the 4h night window -> night_share ~0.182). Not separable "
        "from the substantial-dwell-in-both-windows ambiguous case "
        "(synthetic_ambiguous_e, night_share ~0.235) via any threshold-or-"
        "window-width sweep on night_share (4h-24h, exhaustive, task 13b), "
        "nor via the night/work share ratio (both correctly rejected -- "
        "see task-13b-report.md for the full sweep, run against the real "
        "corpus this fixture replaces). One alternative was NOT tried: an "
        "absolute distinct-night-day COUNT gate rather than a share/ratio "
        "(the real corpus's WFH subject: 11 nights vs. the real ambiguous "
        "subject: 5 nights -- a floor around >=7 would plausibly separate "
        "them with real margin, unlike the day-count RATIO which does "
        "not, since the ambiguous case is present 100% of its own "
        "observed nights, same as the WFH case). Untested because wiring "
        "it in would need compound conditioning against the existing "
        "3-night min_nights home test to avoid a regression, which is "
        "real follow-up work, not something to guess at here. Do not "
        "lower this assertion to match the current 'unlabeled' output."
    ),
)
def test_synthetic_wfh_b_yields_home_only_despite_heavy_working_window_dwell():
    labels = _derive("synthetic_wfh_b", now_ms=NOW_MS)
    kinds = {r.kind for _, r in labels}
    assert "home" in kinds
    # She works from home (the work-from-home shape) — must NOT
    # ALSO produce a distinct 'work' cluster at a different location.
    work_clusters = [c for c, r in labels if r.kind == "work"]
    home_clusters = [c for c, r in labels if r.kind == "home"]
    if work_clusters and home_clusters:
        # If both exist, they must be the SAME cluster (WFH), not two.
        assert work_clusters[0] is home_clusters[0]


def test_synthetic_home_only_c_yields_no_work_place_at_all():
    # The home-only shape: a real home cluster, plus a
    # minor secondary location (3 stays, 3h total across 3 distinct
    # weekdays) that clears cluster_places' min_samples but never
    # _MIN_WORK_DWELL_MS (8h), so it is correctly rejected.
    labels = _derive("synthetic_home_only_c", now_ms=NOW_MS)
    assert not any(r.kind == "work" for _, r in labels)


def test_synthetic_renamed_subject_merged_across_her_rename_yields_one_work_place():
    # The rename case: synthetic_renamed_before and
    # synthetic_renamed_after are ONE subject, one physical desk, split
    # only by a simulated mid-tenure identity rename. This replay merges
    # them manually (production resolves them through `subject_id_for`)
    # to prove the DOMAIN layer produces one clean work
    # cluster once fed the merged fix stream — not two half-people.
    # Neither half alone reaches cluster_places' min_samples=3 (2 stays
    # each), so segmenting them separately would yield NO work cluster at
    # all for either half — the merge is what recovers the signal.
    before = [r for r in CORPUS_ROWS if r[0] == "synthetic_renamed_before" and r[5] == "live"]
    after = [r for r in CORPUS_ROWS if r[0] == "synthetic_renamed_after" and r[5] == "live"]
    merged = sorted(before + after, key=lambda r: r[1])
    fixes = [RawFix(id=i, ts=r[1], lat=r[2], lng=r[3]) for i, r in enumerate(merged)]
    result = segment_stays(
        fixes, subject_id="synthetic_renamed_after", person_id="synthetic_renamed_after",
        circle_id="c1", radius_m=120.0, min_dwell_ms=600_000,
    )
    stays = list(result.closed) + ([result.open_stay] if result.open_stay else [])
    clusters = cluster_places(stays, eps_m=100.0, min_samples=3)
    work = [
        (c, label_place(c, now_ms=NOW_MS, tz_name="Asia/Hong_Kong",
                         min_nights=3, min_weekdays=3, label_freshness_days=45))
        for c in clusters
    ]
    work = [(c, r) for c, r in work if r.kind == "work"]
    assert len(work) == 1, "the merged renamed subject must yield exactly ONE work cluster"


def test_synthetic_ambiguous_e_yields_unlabeled_at_its_ambiguous_cell():
    # The canonical fail-closed proof case (DEC-115) — this cell carries
    # substantial dwell in BOTH the night and working-window terms and must
    # resolve to unlabeled, never a guess either way.
    labels = _derive("synthetic_ambiguous_e", now_ms=NOW_MS)
    ambiguous_cell = [
        (c, r) for c, r in labels
        if abs(sum(s.lat for s in c) / len(c) - 22.420) < 0.01
        and abs(sum(s.lng for s in c) / len(c) - 114.020) < 0.01
    ]
    assert ambiguous_cell, "synthetic_ambiguous_e's cluster must exist in this fixture"
    _cluster, result = ambiguous_cell[0]
    assert result.kind == "unlabeled", (
        "synthetic_ambiguous_e's ambiguous cell must resolve to unlabeled, not a guess"
    )
    # At least one cluster overall must be unlabeled too (loose corroboration).
    assert any(r.kind == "unlabeled" for _, r in labels)


def test_reverting_the_accuracy_gate_would_change_the_result():
    # The accuracy_m <= 100 filter is doing real work, not vacuously true.
    # `CORPUS_ROWS_UNFILTERED` carries the one purpose-built row this fixture
    # uses to prove it: an accuracy_m=110 reading ~100m off
    # synthetic_worker_a's true office centroid, timestamped inside her first
    # work session (3 Aug 2026). Feeding it into segmentation measurably
    # shifts that stay's centroid and inflates its radius.
    assert all(r[4] <= 100 for r in CORPUS_ROWS)
    assert all(r[4] > 100 for r in CORPUS_ROWS_UNFILTERED)
    assert len(CORPUS_ROWS_UNFILTERED) > 0, (
        "if this ever becomes empty the branch this test exercises no "
        "longer applies -- see Task 13 Step 3"
    )

    person = "synthetic_worker_a"
    filtered_rows = sorted(
        (r for r in CORPUS_ROWS if r[0] == person and r[5] == "live"),
        key=lambda r: r[1],
    )
    unfiltered_rows = sorted(
        (r for r in CORPUS_ROWS_UNFILTERED if r[0] == person and r[5] == "live"),
        key=lambda r: r[1],
    )
    merged_rows = sorted(filtered_rows + unfiltered_rows, key=lambda r: r[1])

    def to_fixes(rows):
        return [RawFix(id=i, ts=r[1], lat=r[2], lng=r[3]) for i, r in enumerate(rows)]

    gated = segment_stays(
        to_fixes(filtered_rows), subject_id=person, person_id=person,
        circle_id="c1", radius_m=120.0, min_dwell_ms=600_000,
    )
    reverted = segment_stays(
        to_fixes(merged_rows), subject_id=person, person_id=person,
        circle_id="c1", radius_m=120.0, min_dwell_ms=600_000,
    )

    # The stay spanning the excluded fix's timestamp starts at 09:00 HKT on
    # 3 Aug 2026 (1785718800000) in both runs (the fix falls inside its span,
    # not at its boundary, so segmentation still finds the same started_ts).
    gated_stay = next(s for s in gated.closed if s.started_ts == 1785718800000)
    reverted_stay = next(s for s in reverted.closed if s.started_ts == 1785718800000)

    centroid_shift_m = haversine_distance_m(
        gated_stay.lat, gated_stay.lng, reverted_stay.lat, reverted_stay.lng
    )
    radius_delta_m = reverted_stay.radius_m - gated_stay.radius_m

    assert gated_stay.fix_count == 19
    assert reverted_stay.fix_count == 20, (
        "reverting the gate should pull exactly the one excluded fix into "
        "this stay's group"
    )
    assert centroid_shift_m > 1.0, (
        f"expected a measurable centroid shift, got {centroid_shift_m:.2f}m"
    )
    assert radius_delta_m > 5.0, (
        f"expected the stay's radius to inflate measurably, got "
        f"{radius_delta_m:.2f}m"
    )
