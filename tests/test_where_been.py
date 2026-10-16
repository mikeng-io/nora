"""`where_been` (DEC-155, presence-analytics P1) — the derived-history tool
and its R-31 disclosure seam.

The load-bearing test is the guard matrix: `where_been` must fail-closed in
EXACTLY the matrix `where_is` fails closed in (tests/test_whereis.py is the
shape this file mirrors) — room ⊄ circle → `refused`, an unnamed account →
`refused`, a degraded room → `unavailable` (never a policy answer), and the
`location_trusted` waiver counted. The mobility store is a FAKE here on
purpose: the disclosure decision is room-side, and the store reads are thin
SELECTs whose shapes the store tests own — a fake keeps every guard answer
reachable without a database. The `unlabeled` third state and the R-28
no-egress assertions are the other two contract legs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from nora.agent.room import Room
from nora.agent.security import (
    looks_like_coordinate as _looks_like_coordinate,
)
from nora.agent.tools import build_tool_specs
from nora.agent.whereis import Circle
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
NORA = frozenset({"+85290000100"})
MILES = frozenset({"+85290000101"})
GRANT = frozenset({"+85290000104"})
STRANGER = frozenset({"+85260000000"})
UNNAMED = frozenset({"+85270000000"})  # a phone identity.toml does not map
CIRCLE_ID = "circle-ridgeline"

HK = ZoneInfo("Asia/Hong_Kong")
NOW_MS = int(datetime(2026, 9, 5, 12, 0, tzinfo=HK).timestamp() * 1000)
DAY_MS = 24 * 60 * 60 * 1000
MAX_WINDOW_MS = 90 * DAY_MS

IDENTITY = """
[persons.miles_novak]
first_name = "Miles"
last_name  = "Novak"
aliases = ["Miles"]
[[persons.miles_novak.signal]]
phone = "+85290000101"

[persons.grant_carver]
first_name = "Grant"
last_name  = "Carver"
aliases = ["Chi"]
[[persons.grant_carver.signal]]
phone = "+85290000104"

[persons.pat_ho]
first_name = "Pat"
last_name  = "Ho"
aliases = ["Miles"]
[[persons.pat_ho.signal]]
phone = "+85268886688"

[persons.outsider]
first_name = "Sam"
last_name  = "Outsider"
[[persons.outsider.signal]]
phone = "+85260000000"
"""


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


class _FakeMobility:
    """The four `where_been` reads, recorded. The guard is room-side, so the
    fake's whole job is to answer shaped rows and to say WHICH reads ran (an
    arm must never read the store before the guard passes)."""

    def __init__(self, *, places=None, rhythm=None, trips=None, stays=None):
        self.places = places or []
        self.rhythm = rhythm or []
        self.trips = trips or {"trips": [], "corridors": []}
        self.stays = stays or []
        self.calls: list[tuple] = []

    async def places_with_aggregates(self, *, subject_id):
        self.calls.append(("places", subject_id))
        return list(self.places)

    async def rhythm_bins(self, *, subject_id, window_ms):
        self.calls.append(("rhythm", subject_id, window_ms))
        # The store's own clamp: `window_ms=0` answers empty.
        return list(self.rhythm) if window_ms > 0 else []

    async def trips_with_corridors(self, *, subject_id, window_ms):
        self.calls.append(("trips", subject_id, window_ms))
        return self.trips if window_ms > 0 else {"trips": [], "corridors": []}

    async def stays_for_subject_window(self, *, subject_id, window_ms):
        self.calls.append(("stays", subject_id, window_ms))
        return list(self.stays) if window_ms > 0 else []


class _FakeMobilityWithHistory(_FakeMobility):
    """Adds the full-history reads the `day` arm's `unusual` flags use
    The plain fake lacks them on purpose: that is the
    fail-soft path, and the day must still be answered."""

    def __init__(self, *, history, derived_places, **kw):
        super().__init__(**kw)
        self.history = history
        self.derived_places = derived_places

    async def stays_for_subject(self, *, subject_id):
        self.calls.append(("history", subject_id))
        return list(self.history)

    async def places_for_subject(self, *, subject_id):
        return list(self.derived_places)


MILES_PLACE = {
    "id": 7, "kind": "home", "label": "Home", "saved": True, "lat": 22.31, "lng": 114.16,
    "radius_m": 50.0, "confidence": 0.9, "first_seen_ts": NOW_MS - DAY_MS,
    "last_seen_ts": NOW_MS, "stay_count": 12, "total_dwell_ms": 400_000_000,
}
UNLABELED_PLACE = {
    "id": 8, "kind": "unlabeled", "label": None, "saved": False, "lat": 22.44, "lng": 114.02,
    "radius_m": 80.0, "confidence": 0.42, "first_seen_ts": NOW_MS - DAY_MS,
    "last_seen_ts": NOW_MS, "stay_count": 3, "total_dwell_ms": 40_000_000,
}
RHYTHM_BINS = [{"hour_of_week": 40, "stay_count": 3, "total_dwell_ms": 7_200_000}]
TRIPS = {
    "trips": [{
        "id": 1, "started_ts": NOW_MS - 3_600_000, "ended_ts": NOW_MS - 1_800_000,
        "distance_m": 4200.0, "avg_speed_kmh": 28.0, "fix_count": 12,
        "corridor_id": 7, "corridor_sample_count": 5,
    }],
    "corridors": [{
        "id": 7, "sample_count": 5, "first_seen_ts": NOW_MS - 5 * DAY_MS,
        "last_seen_ts": NOW_MS, "sequence": ["Tai Ho Road", "Castle Peak Road"],
    }],
}
# One stay and one trip inside 4 Sep 2026 (HK), plus one of each the next
# morning — the day filter must keep the in-day rows and drop the rest.
DAY_STAY_TS = int(datetime(2026, 9, 4, 9, 0, tzinfo=HK).timestamp() * 1000)
DAY_TRIP_TS = int(datetime(2026, 9, 4, 18, 0, tzinfo=HK).timestamp() * 1000)
NEXT_DAY_STAY_TS = int(datetime(2026, 9, 5, 1, 0, tzinfo=HK).timestamp() * 1000)
DAY_TRIPS = {
    "trips": [{
        "id": 2, "started_ts": DAY_TRIP_TS, "ended_ts": DAY_TRIP_TS + 1_800_000,
        "distance_m": 3100.0, "avg_speed_kmh": 22.0, "fix_count": 9,
        "corridor_id": 7, "corridor_sample_count": 5,
    }],
    "corridors": [],
}
DAY_STAYS = [
    {"started_ts": NEXT_DAY_STAY_TS, "ended_ts": NEXT_DAY_STAY_TS + 600_000,
     "lat": 22.410, "lng": 114.124, "place_kind": None, "place_label": None},
    {"started_ts": DAY_STAY_TS, "ended_ts": DAY_STAY_TS + 3_600_000,
     "lat": 22.410, "lng": 114.124, "place_kind": "home", "place_label": "Home"},
]


def _circle(persons, *, with_pat=False) -> Circle:
    # `people` carries the RESOLVED roster — the same shape
    # `circles_from_persons` builds from identity.toml: real `PersonRef`s with
    # their aliases, so the R-47 ask-about side resolves the way production
    # resolves it.
    # Pat shares Miles's alias 「Miles」 — the ambiguous-name fixture needs
    # BOTH matching people ask-about-able (the circle joined with the room),
    # so `with_pat` puts him on this circle's roster too.
    ids = (
        ("grant_carver", "miles_novak", "pat_ho") if with_pat
        else ("grant_carver", "miles_novak")
    )
    people = tuple(persons.by_person[pid] for pid in ids)
    return Circle(
        circle_id=CIRCLE_ID, person_ids=frozenset(ids), people=people,
    )


def _room(persons, *, members, fail=False, tagger=None):
    return Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: tuple(members)}, fail=fail),
        persons=persons, self_ids=NORA, tagger=tagger,
    )


class _FakeFixes:
    """The raw-fix edge `where_been` reads through the SAME `latest_fix` seam
    `where_is` uses: person_id -> the ts of their newest fix."""

    def __init__(self, last_ts: dict[str, int]):
        self._last_ts = last_ts

    async def latest_fix(self, *, circle_id, person_id):
        ts = self._last_ts.get(person_id)
        return None if ts is None else SimpleNamespace(ts=ts)


def _where_been(
    persons, mobility, *, members, fail=False, location_trusted=False,
    now_ms=NOW_MS, metrics=None, with_pat=False, tagger=None, fixes=None,
):
    """The built `where_been` closure, through `build_tool_specs` — the same
    wiring the tag path uses (registry -> specs -> call), with the registry's
    own clock and the guard's circles threaded exactly as composition does."""
    specs = build_tool_specs(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=_room(persons, members=members, fail=fail, tagger=tagger),
        transit_source=None,
        presence_circles=(_circle(persons, with_pat=with_pat),),
        location_trusted=location_trusted,
        mobility=mobility, clock=lambda: datetime.fromtimestamp(now_ms / 1000, tz=UTC),
        presence_source=fixes,
    )
    return next(s for s in specs if s.name == "where_been").call


def _labelled(metrics, name, label_key, label_value):
    """`Metrics.snapshot()` keys samples `(name, sorted label pairs)`; this
    says whether one sample of `name` carries the given label value."""
    return any(
        n == name and (label_key, label_value) in labels
        for (n, labels), _value in metrics.snapshot().items()
    )


# ---- the R-31 guard matrix — the same fail-closed answers `where_is` gives ----


async def test_where_been_refuses_a_room_not_subset_of_the_circle(persons):
    """R-31: a stranger in the room is outside the circle — REFUSED, never a
    silent omission and never a read. Identical to `where_is`'s own refusal."""
    mobility = _FakeMobility(places=[dict(MILES_PLACE)])
    metrics = Metrics()
    specs = build_tool_specs(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=metrics,
        room=_room(persons, members=(MILES, GRANT, STRANGER)),
        transit_source=None, presence_circles=(_circle(persons),),
        mobility=mobility,
        clock=lambda: datetime.fromtimestamp(NOW_MS / 1000, tz=UTC),
    )
    tool = next(s for s in specs if s.name == "where_been").call
    out = await tool("places", name="Miles")
    assert out["status"] == "refused"
    assert "note" in out
    # The guard runs BEFORE any store read — the refusal is not data-shaped.
    assert mobility.calls == []
    assert _labelled(metrics, "nora_presence_disclosure_refusals_total", "reason", "not_subset")


async def test_where_been_refuses_an_unnamed_account(persons):
    """An account the room holds but identity.toml cannot map is, by
    definition, unverifiable against the circle — the LIKELIEST real trigger,
    refused unless the room is location-trusted (DEC-79)."""
    mobility = _FakeMobility()
    specs = build_tool_specs(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, UNNAMED)),
        transit_source=None, presence_circles=(_circle(persons),), mobility=mobility,
        clock=lambda: datetime.fromtimestamp(NOW_MS / 1000, tz=UTC),
    )
    tool = next(s for s in specs if s.name == "where_been").call
    out = await tool("rhythm", name="Miles")
    assert out["status"] == "refused"
    assert mobility.calls == []


async def test_where_been_degraded_room_is_unavailable_not_refused(persons):
    """A room she cannot READ is an ERROR, not a policy decision — the split
    R-27 draws, as `where_is` does.

    `fail=True` plus a tagger is the TRUE degraded path: `Room.scope` narrows
    to the tagger with `degraded=True` rather than going dark. Without the
    tagger the same fail would hit `available=False` instead, which is a
    different branch (`I can't work out who is in this group`).
    """
    mobility = _FakeMobility()
    metrics = Metrics()
    tagger = persons.resolve("signal", "+85290000101")
    tool = _where_been(
        persons, mobility, members=(MILES,), fail=True,
        tagger=tagger, metrics=metrics,
    )
    out = await tool("places", name="Miles")
    assert out["status"] == "unavailable"          # not "refused"
    assert "circle" not in out["note"]             # no false claim about the room
    assert mobility.calls == []
    assert _labelled(metrics, "nora_presence_disclosure_refusals_total", "reason", "degraded")


async def test_where_been_counts_the_location_trusted_waiver(persons):
    """DEC-79: a location-trusted room waives the unnamed refusal — and the
    waiver is COUNTED, never silent, the exact rule `Whereis.locate` runs."""
    mobility = _FakeMobility(places=[dict(MILES_PLACE)])
    metrics = Metrics()
    tool = _where_been(
        persons, mobility, members=(MILES, UNNAMED),
        location_trusted=True, metrics=metrics,
    )
    out = await tool("places", name="Miles")
    assert out["status"] == "ok"
    # A WAIVED guard is counted, not silent — the DEC-79 instrument.
    assert _labelled(metrics, "nora_presence_disclosure_waived_total", "reason", "unnamed")
    assert ("places", "miles_novak") in mobility.calls


async def test_where_been_resolves_a_circle_member_outside_the_room(persons):
    """R-47: who may be ASKED ABOUT resolves against the CIRCLE unioned with
    the room — Grant is not in this room but shares the circle, so the
    answer is his derived pattern, never «nobody here goes by that»."""
    mobility = _FakeMobility(places=[dict(MILES_PLACE)])
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("places", name="Chi")
    assert out["status"] == "ok"
    assert out["people"][0]["person"] == "Grant Carver"
    assert ("places", "grant_carver") in mobility.calls


# ---- the six-state contract -------------------------------------------------


async def test_where_been_not_found_and_ambiguous(persons):
    mobility = _FakeMobility()
    tool = _where_been(persons, mobility, members=(MILES,))
    miss = await tool("places", name="Nobody")
    assert miss["status"] == "not_found"
    assert "note" in miss
    assert mobility.calls == []
    # Two people in THIS room go by 「Miles」 (miles's alias and Pat's) — the
    # resolution asks which one, exactly as `where_is` does.
    pat = frozenset({"+85268886688"})
    clash_tool = _where_been(
        persons, mobility, members=(MILES, pat), with_pat=True,
    )
    clash = await clash_tool("places", name="Miles")
    assert clash["status"] == "ambiguous"
    assert set(clash["could_be"]) == {"Miles Novak", "Pat Ho"}
    assert mobility.calls == []  # still no read behind an unresolved name


async def test_where_been_answers_the_room_when_no_name_is_given(persons):
    """name=None (or 「大家」) is the group asking about the group: one entry
    per person the substrate has derived data for, and `nothing_found` — not
    an error — when nobody has any."""
    mobility = _FakeMobility(places=[dict(MILES_PLACE)])
    tool = _where_been(persons, mobility, members=(MILES, GRANT))
    out = await tool("places")
    assert out["status"] == "ok"
    assert out["window"] == {"days": 7}
    assert sorted(p["person"] for p in out["people"]) == [
        "Grant Carver", "Miles Novak",
    ]
    empty = _FakeMobility()
    tool = _where_been(persons, empty, members=(MILES, GRANT))
    out = await tool("places", name="大家")
    # The read WORKED and found nothing — `nothing_found`, not `unavailable`
    # (R-27): the store was consulted for every room member, honestly empty.
    assert out["status"] == "nothing_found"
    assert ("places", "miles_novak") in empty.calls
    assert ("places", "grant_carver") in empty.calls


async def test_where_been_unwired_is_unavailable_not_nothing_found(persons):
    """mobility=None is the honest not-wired state (offline/eval
    compositions): «I could not check», never «no derived pattern» (R-5)."""
    tool = _where_been(persons, None, members=(MILES,))
    out = await tool("places", name="Miles")
    assert out["status"] == "unavailable"
    assert "note" in out


async def test_where_been_the_what_vocabulary_is_closed(persons):
    """Any `what` outside the four arms is ONE `unavailable` — fail-closed,
    never a silent `{}` and never a guessed arm."""
    mobility = _FakeMobility(places=[dict(MILES_PLACE)])
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("anomaly")
    assert out["status"] == "unavailable"
    assert "note" in out
    assert mobility.calls == []


# ---- ADR 0015 §3: how to report a status rides in the result -----------------


class _BrokenMobility(_FakeMobility):
    async def places_with_aggregates(self, *, subject_id):
        raise ConnectionError("mobility store unreachable")


async def test_every_non_ok_status_carries_a_note(persons):
    """ADR 0015 §3/§6: the description only NAMES the statuses; what to say
    for each is the result's `note`. Every non-ok code path is driven here, so
    a path that loses its note (the reporting rule for that status) goes red."""
    pat = frozenset({"+85268886688"})
    tagger = persons.resolve("signal", "+85290000101")
    empty = _FakeMobility()
    cases = {
        "unavailable/what": (_where_been(persons, empty, members=(MILES,)), ("anomaly",), {}),
        "unavailable/unwired": (_where_been(persons, None, members=(MILES,)), ("places",), {}),
        "unavailable/window": (
            _where_been(persons, empty, members=(MILES,)), ("rhythm",), {"days": "a week"},
        ),
        "unavailable/date": (
            _where_been(persons, empty, members=(MILES,)), ("day",), {"date": "yesterday"},
        ),
        "unavailable/room": (
            _where_been(persons, empty, members=(MILES,), fail=True), ("places",), {},
        ),
        "unavailable/degraded": (
            _where_been(persons, empty, members=(MILES,), fail=True, tagger=tagger),
            ("places",), {},
        ),
        "unavailable/read": (
            _where_been(persons, _BrokenMobility(), members=(MILES,)),
            ("places",), {"name": "Miles"},
        ),
        "refused": (
            _where_been(persons, empty, members=(MILES, STRANGER)), ("places",), {},
        ),
        "not_found": (
            _where_been(persons, empty, members=(MILES,)), ("places",), {"name": "Nobody"},
        ),
        "ambiguous": (
            _where_been(persons, empty, members=(MILES, pat), with_pat=True),
            ("places",), {"name": "Miles"},
        ),
        "nothing_found": (
            _where_been(persons, empty, members=(MILES,)), ("places",), {"name": "Miles"},
        ),
    }
    for case, (tool, args, kwargs) in cases.items():
        out = await tool(*args, **kwargs)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case


async def test_an_ok_note_carries_the_rules_its_answer_needs(persons):
    """The reporting rules that left the description (the numbers carry their
    window; `unlabeled` is no pattern, never a place; an unmatched stay's
    lat/lng is never read as a neighbourhood) arrive in the `ok`
    they are about, and a clause arrives only when its field is there."""
    mobility = _FakeMobility(
        places=[dict(UNLABELED_PLACE), dict(MILES_PLACE)], rhythm=list(RHYTHM_BINS),
        stays=[dict(s) for s in DAY_STAYS], trips=DAY_TRIPS,
    )
    tool = _where_been(persons, mobility, members=(MILES,))

    places = await tool("places", name="Miles")
    assert "unlabeled" in places["note"]
    labelled_only = await _where_been(
        persons, _FakeMobility(places=[dict(MILES_PLACE)]), members=(MILES,),
    )("places", name="Miles")
    assert "unlabeled" not in labelled_only["note"]

    rhythm = await tool("rhythm", name="Miles", days=14)
    assert "past 14 days" in rhythm["note"]

    day = await tool("day", name="Miles", date="2026-09-04")
    assert "2026-09-04" in day["note"]
    assert "neighbourhood" not in day["note"]   # the in-day stay is labelled home
    unmatched = dict(DAY_STAYS[1], place_kind=None, place_label=None)
    day = await _where_been(
        persons, _FakeMobility(stays=[unmatched]), members=(MILES,),
    )("day", name="Miles", date="2026-09-04")
    assert "neighbourhood" in day["note"]


# ---- unlabeled survives end-to-end ------------------------------------------


async def test_unlabeled_kind_survives_end_to_end(persons):
    """`unlabeled` is a precise, load-bearing answer, not a
    gap — the fixture place's kind comes back AS `unlabeled`, and the model-
    facing contract (the docstring) forbids smoothing it into a place."""
    mobility = _FakeMobility(places=[dict(UNLABELED_PLACE), dict(MILES_PLACE)])
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("places", name="Miles")
    assert out["status"] == "ok"
    kinds = [p["kind"] for p in out["people"][0]["places"]]
    assert kinds == ["unlabeled", "home"]
    assert out["people"][0]["places"][0]["name"] == "a place they go"
    assert "label" not in out["people"][0]["places"][0]


# ---- R-28 after DEC-156: sequence/radius never egress; day-stay lat/lng do ---


async def test_r28_speech_boundary_after_dec156(persons):
    """DEC-156 narrowed R-28: the day arm's stays now CARRY lat/lng (the
    fetch surface — the model may hand them to a street-view/maps tool), so
    the old no-coordinate-anywhere scan is gone. What still holds: the
    corridor road sequence NEVER leaves the tool, radius_m never does,
    and the SPEECH half of R-28 is intact — coordinate strings are still
    refused in replies."""
    mobility = _FakeMobility(
        places=[dict(MILES_PLACE), dict(UNLABELED_PLACE)], rhythm=list(RHYTHM_BINS),
        trips=TRIPS, stays=[dict(s) for s in DAY_STAYS],
    )
    tool = _where_been(persons, mobility, members=(MILES,))

    def _walk(node, arm):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in ("sequence", "radius_m"), (
                    f"{arm}: {key} leaked into the result"
                )
                _walk(value, arm)
        elif isinstance(node, list):
            for item in node:
                _walk(item, arm)

    for what in ("places", "rhythm", "routes", "day"):
        out = await tool(what, name="Miles", date="2026-09-04")
        _walk(out, what)
    day_out = await tool("day", name="Miles", date="2026-09-04")
    stays = day_out["people"][0]["stays"]
    assert stays[0]["lat"] == 22.410 and stays[0]["lng"] == 114.124
    # The speech half of R-28 still refuses coordinate strings in replies.
    assert _looks_like_coordinate("22.31,114.16")


async def test_day_carries_iso_timestamps_and_latlng(persons):
    """DEC-156: unix-ms alone is unreadable to the model, and converting it
    burns the budget on calculate calls. Every
    timestamp the tool emits carries an RFC 3339 form beside the raw ms, and
    the day arm's stays carry the stay centroid for the fetch surface."""
    mobility = _FakeMobility(
        stays=[dict(s) for s in DAY_STAYS], trips=DAY_TRIPS,
    )
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("day", name="Miles", date="2026-09-04")
    assert out["status"] == "ok"
    stays = out["people"][0]["stays"]
    assert stays[0]["started_ts"] == DAY_STAY_TS
    expected = datetime.fromtimestamp(
        DAY_STAY_TS / 1000, tz=HK,
    ).isoformat(timespec="seconds")
    assert stays[0]["started_iso"] == expected
    assert stays[0]["ended_iso"] == datetime.fromtimestamp(
        (DAY_STAY_TS + 3_600_000) / 1000, tz=HK,
    ).isoformat(timespec="seconds")
    assert stays[0]["lat"] == 22.410 and stays[0]["lng"] == 114.124
    trips = out["people"][0]["trips"]
    assert trips[0]["started_iso"] == datetime.fromtimestamp(
        DAY_TRIP_TS / 1000, tz=HK,
    ).isoformat(timespec="seconds")


async def test_places_and_routes_arms_carry_iso_timestamps(persons):
    """DEC-156: places first/last seen and routes trips/corridors carry the
    ISO forms too — no arm leaves the model with bare epoch values."""
    mobility = _FakeMobility(
        places=[dict(MILES_PLACE)], rhythm=list(RHYTHM_BINS),
        trips=TRIPS, stays=[],
    )
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("places", name="Miles")
    place = out["people"][0]["places"][0]
    # The words route takes back this turn, never an id or a coordinate
    # (DEC-216 M3).
    assert place["name"] == "Home" and "place_ref" not in place
    assert "lat" not in place and "lng" not in place
    assert place["first_seen_iso"] == datetime.fromtimestamp(
        MILES_PLACE["first_seen_ts"] / 1000, tz=HK,
    ).isoformat(timespec="seconds")
    assert place["last_seen_iso"] == datetime.fromtimestamp(
        MILES_PLACE["last_seen_ts"] / 1000, tz=HK,
    ).isoformat(timespec="seconds")
    routes = await tool("routes", name="Miles")
    trip = routes["people"][0]["trips"][0]
    assert trip["started_iso"] == datetime.fromtimestamp(
        (NOW_MS - 3_600_000) / 1000, tz=HK,
    ).isoformat(timespec="seconds")
    corridor = routes["people"][0]["corridors"][0]
    assert corridor["first_seen_iso"] == datetime.fromtimestamp(
        (NOW_MS - 5 * DAY_MS) / 1000, tz=HK,
    ).isoformat(timespec="seconds")
    assert "sequence" not in corridor
# ---- windows: days clamping, date handling ----------------------------------


async def test_days_zero_answers_an_empty_window(persons):
    """days=0 is an empty window: the read WORKS and answers nothing —
    `nothing_found`, never `unavailable` (R-27 keeps the two apart)."""
    mobility = _FakeMobility(rhythm=list(RHYTHM_BINS))
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("rhythm", name="Miles", days=0)
    assert out["status"] == "nothing_found"
    assert ("rhythm", "miles_novak", 0) in mobility.calls


async def test_days_clamped_at_the_store_ceiling(persons):
    """days=1000 caps at 90 — the same bound the store clamps, so the answer
    names its window honestly."""
    mobility = _FakeMobility(rhythm=list(RHYTHM_BINS))
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("rhythm", name="Miles", days=1000)
    assert out["status"] == "ok"
    assert out["window"] == {"days": 90}
    assert ("rhythm", "miles_novak", MAX_WINDOW_MS) in mobility.calls


async def test_day_honours_the_date_and_filters_the_upper_edge(persons):
    """`date` is the day they mean: the fetch window starts at that day's
    midnight (the store's cutoff lands on it) and the day's upper edge is
    filtered in the tool — tomorrow-morning rows do not leak into yesterday."""
    mobility = _FakeMobility(
        stays=[dict(s) for s in DAY_STAYS], trips=DAY_TRIPS,
    )
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("day", name="Miles", date="2026-09-04")
    assert out["status"] == "ok"
    assert out["window"] == {"date": "2026-09-04"}
    day_start_ms = int(datetime(2026, 9, 4, tzinfo=HK).timestamp() * 1000)
    fetch_ms = NOW_MS - day_start_ms
    assert ("stays", "miles_novak", fetch_ms) in mobility.calls
    stays = out["people"][0]["stays"]
    assert [s["started_ts"] for s in stays] == [DAY_STAY_TS]
    # places visited, from the substrate's own linkage: kind/label only.
    assert stays[0]["place_kind"] == "home"
    # The words every presence answer uses, never the raw label: this one's
    # fixture carries no saved flag, so its label is only a street.
    assert stays[0]["place"] == "a home of theirs near Home"
    assert "place_label" not in stays[0]
    trips = out["people"][0]["trips"]
    assert [t["started_ts"] for t in trips] == [DAY_TRIP_TS]
    assert ("trips", "miles_novak", fetch_ms) in mobility.calls


async def test_day_outside_the_bounded_window_is_nothing_found(persons):
    """A date older than the 90-day bound is honest emptiness — the store
    would otherwise clamp and serve a PARTIAL day as if whole."""
    mobility = _FakeMobility(stays=[dict(s) for s in DAY_STAYS])
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("day", name="Miles", date="2026-01-01")
    assert out["status"] == "nothing_found"
    assert mobility.calls == []


async def test_day_defaults_to_today_and_rejects_a_malformed_date(persons):
    mobility = _FakeMobility(stays=[dict(s) for s in DAY_STAYS])
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("day", name="Miles")
    assert out["status"] == "ok"
    assert out["window"] == {"date": "2026-09-05"}
    bad = await tool("day", name="Miles", date="September 4")
    assert bad["status"] == "unavailable"


async def test_date_is_ignored_outside_the_day_arm(persons):
    """`date` is a day-arm argument only: rhythm answers in days and carries
    its window, whatever a stray `date` argument says."""
    mobility = _FakeMobility(rhythm=list(RHYTHM_BINS))
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("rhythm", name="Miles", date="2026-09-04")
    assert out["status"] == "ok"
    assert out["window"] == {"days": 7}
    assert ("rhythm", "miles_novak", 7 * DAY_MS) in mobility.calls


# ---- the routes arm keeps the linkage, drops the sequence --------------------


async def test_routes_carry_linkage_without_the_sequence(persons):
    mobility = _FakeMobility(trips=TRIPS)
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("routes", name="Miles")
    assert out["status"] == "ok"
    payload = out["people"][0]
    assert payload["trips"][0]["corridor_id"] == 7
    assert payload["corridors"][0]["sample_count"] == 5
    assert "sequence" not in payload["corridors"][0]


# ---- the record's edge -------------------------------------------------------
#
# If the collector stops (say its Life360 login is lost at 00:12), a later
# question would get a home stay ending 00:12 and then nothing, and the model
# could read the nothing as «stayed home». The derived facts stop where the raw
# fixes stop, so the answer carries that edge.

# The last fix 00:12 today, asked at noon — a home stay from yesterday evening
# ending exactly there.
LAST_FIX_TS = int(datetime(2026, 9, 5, 0, 12, tzinfo=HK).timestamp() * 1000)
STAY_TO_EDGE = [
    {"started_ts": LAST_FIX_TS - 7 * 3_600_000, "ended_ts": LAST_FIX_TS,
     "lat": 22.410, "lng": 114.124, "place_kind": "home", "place_label": "Home"},
]


def _iso(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=HK).isoformat(timespec="seconds")


async def test_day_says_where_the_record_stops(persons):
    """History that ends at the last fix names that edge, so
    an empty stretch after it cannot read as the person staying put."""
    mobility = _FakeMobility(stays=[dict(s) for s in STAY_TO_EDGE])
    fixes = _FakeFixes({"miles_novak": LAST_FIX_TS})
    tool = _where_been(persons, mobility, members=(MILES,), fixes=fixes)
    out = await tool("day", name="Miles")
    assert out["status"] == "ok"
    assert out["people"][0]["last_recorded_iso"] == _iso(LAST_FIX_TS)
    assert out["recording_stopped"] == [
        {"person": "Miles Novak", "last_recorded_iso": _iso(LAST_FIX_TS)},
    ]
    assert "missing" in out["recording_note"]


async def test_nothing_found_still_says_where_the_record_stops(persons):
    """`nothing_found` for today carries the edge too: with no rows at all, the
    edge is the only thing that separates «stayed put» from «nothing was
    recorded»."""
    mobility = _FakeMobility()
    fixes = _FakeFixes({"miles_novak": LAST_FIX_TS})
    tool = _where_been(persons, mobility, members=(MILES,), fixes=fixes)
    out = await tool("day", name="Miles")
    assert out["status"] == "nothing_found"
    assert out["recording_stopped"] == [
        {"person": "Miles Novak", "last_recorded_iso": _iso(LAST_FIX_TS)},
    ]


async def test_a_current_record_carries_its_edge_but_no_stop(persons):
    """A fix inside one missed heartbeat of now is a live record: the edge is
    still carried, and nothing claims the recording stopped."""
    recent = NOW_MS - 10 * 60_000
    mobility = _FakeMobility(stays=[dict(s) for s in STAY_TO_EDGE])
    fixes = _FakeFixes({"miles_novak": recent})
    tool = _where_been(persons, mobility, members=(MILES,), fixes=fixes)
    out = await tool("day", name="Miles")
    assert out["status"] == "ok"
    assert out["people"][0]["last_recorded_iso"] == _iso(recent)
    assert "recording_stopped" not in out


async def test_a_past_day_the_record_covers_is_not_stopped(persons):
    """The edge is measured against the WINDOW's end: yesterday ends before
    the last fix, so yesterday's history is whole."""
    mobility = _FakeMobility(stays=[dict(s) for s in DAY_STAYS], trips=DAY_TRIPS)
    fixes = _FakeFixes({"miles_novak": LAST_FIX_TS})
    tool = _where_been(persons, mobility, members=(MILES,), fixes=fixes)
    out = await tool("day", name="Miles", date="2026-09-04")
    assert out["status"] == "ok"
    assert "recording_stopped" not in out


# ---- the day arm's `unusual` flags ------------------------------------------


def _office_history():
    from nora.domain.mobility import Stay

    rows = []
    for d in range(1, 11):  # ten ordinary office days, 09:00-18:00, before the day asked
        day = datetime(2026, 8, 20, tzinfo=HK) + timedelta(days=d)
        rows.append(Stay(
            subject_id="miles_novak", person_id="miles_novak", circle_id="c",
            started_ts=int((day + timedelta(hours=9)).timestamp() * 1000),
            ended_ts=int((day + timedelta(hours=18)).timestamp() * 1000),
            lat=22.28, lng=114.18, radius_m=30.0, fix_count=9, place_id=3,
        ))
    return rows


async def test_the_day_arm_flags_a_stay_unusual_against_their_own_past(persons):
    """Still at the office at 23:00 when they always leave at 18:00: the day's
    stay carries the comparison, and the note says how to say it."""
    from nora.domain.mobility import DerivedPlace, Stay

    late_start = int(datetime(2026, 9, 4, 9, 0, tzinfo=HK).timestamp() * 1000)
    late = Stay(
        subject_id="miles_novak", person_id="miles_novak", circle_id="c",
        started_ts=late_start, ended_ts=late_start + 14 * 3_600_000,
        lat=22.28, lng=114.18, radius_m=30.0, fix_count=12, place_id=3,
    )
    mobility = _FakeMobilityWithHistory(
        history=[*_office_history(), late],
        derived_places=[DerivedPlace(3, "miles_novak", "c", 22.28, 114.18, 100.0,
                                     "work", 1.0, "Office", None)],
        stays=[{"started_ts": late.started_ts, "ended_ts": late.ended_ts,
                "lat": 22.28, "lng": 114.18, "place_kind": "work",
                "place_label": "Office"}],
    )
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("day", name="Miles", date="2026-09-04")
    assert out["status"] == "ok"
    [stay] = out["people"][0]["stays"]
    kinds = {f["kind"]: f for f in stay["unusual"]}
    assert kinds["departure"] == {"kind": "departure", "at": "23:00", "usually": "18:00"}
    assert kinds["duration"]["minutes"] == 14 * 60 and kinds["duration"]["usual_minutes"] == 9 * 60
    assert "never as a judgement" in out["note"]


async def test_an_ordinary_day_carries_no_unusual_key(persons):
    from nora.domain.mobility import DerivedPlace

    history = _office_history()
    ordinary = history[-1]
    mobility = _FakeMobilityWithHistory(
        history=history,
        derived_places=[DerivedPlace(3, "miles_novak", "c", 22.28, 114.18, 100.0,
                                     "work", 1.0, "Office", None)],
        stays=[{"started_ts": ordinary.started_ts, "ended_ts": ordinary.ended_ts,
                "lat": 22.28, "lng": 114.18, "place_kind": "work",
                "place_label": "Office"}],
    )
    day = datetime.fromtimestamp(ordinary.started_ts / 1000, tz=HK).date().isoformat()
    out = await _where_been(persons, mobility, members=(MILES,))("day", name="Miles", date=day)
    [stay] = out["people"][0]["stays"]
    assert "unusual" not in stay
    assert "never as a judgement" not in out["note"]


async def test_the_day_is_answered_when_the_flags_cannot_be_computed(persons):
    """Fail-soft: the plain fake has no history read; the day still answers."""
    mobility = _FakeMobility(stays=list(DAY_STAYS), trips=DAY_TRIPS)
    tool = _where_been(persons, mobility, members=(MILES,))
    out = await tool("day", name="Miles", date="2026-09-04")
    assert out["status"] == "ok"
    assert all("unusual" not in s for s in out["people"][0]["stays"])


# ---- the routes arm's usual trips, and places' refs --------------------------


async def test_the_routes_arm_says_how_long_their_repeated_trips_take(persons):
    from nora.domain.mobility import DerivedPlace, Stay, Trip

    def at(day, hours):
        return int((day + timedelta(hours=hours)).timestamp() * 1000)

    stays, trips = [], []
    for d in range(5):
        day = datetime(2026, 8, 24, tzinfo=HK) + timedelta(days=d)  # Mon-Fri
        minutes = 35 + d * 2
        stays.append(Stay("miles_novak", "miles_novak", "c", at(day, 9), at(day, 18),
                          22.28, 114.18, 30.0, 9, place_id=3))
        stays.append(Stay("miles_novak", "miles_novak", "c", at(day, 18 + minutes / 60),
                          at(day, 30), 22.47, 114.0, 30.0, 9, place_id=1))
        trips.append(Trip("miles_novak", "miles_novak", "c", at(day, 18),
                          at(day, 18 + minutes / 60), 20_000.0, 40.0, 30, ()))
    mobility = _FakeMobilityWithHistory(
        history=stays,
        derived_places=[
            DerivedPlace(
                1, "miles_novak", "c", 22.47, 114.0, 100.0, "home", 1.0, "Miles Home", "t",
            ),
            DerivedPlace(3, "miles_novak", "c", 22.28, 114.18, 100.0, "work", 1.0, None, None),
        ],
        trips=TRIPS,
    )
    mobility.trips_for_subject = lambda *, subject_id: _async(trips)  # type: ignore[method-assign]
    out = await _where_been(persons, mobility, members=(MILES,))("routes", name="Miles")
    [usual] = out["people"][0]["usual_trips"]
    # «Miles Home» is saved (its tag id): said by its name.
    assert usual == {
        "from": "their work", "to": "Miles Home", "when": "weekday evenings",
        "usually_takes": "about 39 min",
    }


async def _async(value):
    return value


async def test_routes_are_answered_when_usual_trips_cannot_be_worked_out(persons):
    mobility = _FakeMobility(trips=TRIPS)  # no history reads at all
    out = await _where_been(persons, mobility, members=(MILES,))("routes", name="Miles")
    assert out["status"] == "ok" and "usual_trips" not in out["people"][0]


async def test_an_unsaved_label_never_reaches_her_as_a_label(persons):
    """A geocoded street beside `name` must not be read as a road he passed,
    even with 26 hours of stays next to it."""
    street = dict(UNLABELED_PLACE, label="Main Road", saved=False)
    tool = _where_been(persons, _FakeMobility(places=[street]), members=(MILES,))
    [place] = (await tool("places", name="Miles"))["people"][0]["places"]
    assert place["name"] == "a place they go near Main Road"
    assert "label" not in place and "Main Road" not in str(
        {k: v for k, v in place.items() if k != "name"})


async def test_the_day_names_a_saved_place_by_its_name_and_an_unsaved_one_near_its_street(persons):
    saved = dict(DAY_STAYS[1], place_label="Home", place_saved=True)
    street = dict(DAY_STAYS[1], started_ts=DAY_STAYS[1]["started_ts"] + 1,
                  place_kind="unlabeled", place_label="Main Road", place_saved=False)
    mobility = _FakeMobility(stays=[saved, street])
    out = await _where_been(persons, mobility, members=(MILES,))("day", name="Miles")
    names = sorted(s["place"] for s in out["people"][0]["stays"])
    assert names == ["Home", "a place they go near Main Road"]


async def test_no_rollup_build_yet_is_unavailable_not_nothing_found(persons, pg_url):
    """A fresh database has no current Rollup build until the analytics
    process's first rebuild switches one in: `where_been` then answers
    `unavailable`, never an empty `nothing_found` («no places») about data
    nobody derived (R-27). The real SQL store, on a real fresh database; the
    same store with a build is the positive control."""
    from nora.infra.sql import create_sql_engine
    from nora.infra.sql.mobility import SqlMobilityStore

    engine = create_sql_engine(pg_url)
    try:
        mobility = SqlMobilityStore(engine)
        for what in ("places", "rhythm", "routes"):
            out = await _where_been(persons, mobility, members=(MILES,))(what, name="Miles")
            assert out["status"] == "unavailable", what
        await mobility._seed_build_for_test(current=True)
        out = await _where_been(persons, mobility, members=(MILES,))("places", name="Miles")
        assert out["status"] == "nothing_found"
    finally:
        await engine.dispose()
