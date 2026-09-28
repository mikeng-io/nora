"""`routine_read` (design/proposed/27-presence-analytics.md §4.3, P1b).

«Where will Grant be on Wednesday night?» answered from the Tier 4
forecast, behind `where_been`'s own guard (`_presence_audience`). The legs:
every status with its note; the guard matrix (refused, unnamed, degraded,
waiver counted, not_found, ambiguous) with NO store read before it passes; the
answer carrying its evidence (window, basis, weeks, observed share) and no
coordinate (R-28); and a weak basis said inside the routine as «roughly».
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from nora.agent.tools import build_tool_specs
from nora.domain.mobility import DerivedPlace, Stay
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from tests.test_where_been import (
    GRANT,
    HK,
    IDENTITY,
    MILES,
    STRANGER,
    UNNAMED,
    _circle,
    _labelled,
    _room,
)

# Saturday 2026-09-05 12:00 HKT: the history below ends before it.
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=HK)
HOME, SECOND, GYM = 11, 12, 13
_AT = {HOME: (22.388, 114.037), SECOND: (22.410, 114.177), GYM: (22.300, 114.170)}


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _stay(day: datetime, start_h: float, end_h: float, place: int | None) -> Stay:
    lat, lng = _AT[place] if place is not None else (22.1, 114.0)
    return Stay(
        subject_id="grant_carver", person_id="grant_carver", circle_id="c",
        started_ts=int((day + timedelta(hours=start_h)).timestamp() * 1000),
        ended_ts=int((day + timedelta(hours=end_h)).timestamp() * 1000),
        lat=lat, lng=lng, radius_m=30.0, fix_count=6, place_id=place,
    )


def _history(weeks: int) -> list[Stay]:
    """Home every night except Tuesday, which is the second home; the gym
    on Wednesday 19-21, then home. `weeks` whole weeks ending before NOW."""
    first_monday = datetime(2026, 8, 31, tzinfo=HK) - timedelta(weeks=weeks)
    stays: list[Stay] = []
    for w in range(weeks):
        for d in range(7):
            day = first_monday + timedelta(days=7 * w + d)
            if d == 2:
                stays.append(_stay(day, 19, 21, GYM))
                stays.append(_stay(day, 21.5, 31, HOME))
            else:
                stays.append(_stay(day, 19, 31, SECOND if d == 1 else HOME))
    return stays


PLACES = [
    DerivedPlace(HOME, "grant_carver", "c", *_AT[HOME], 100.0, "home", 1.0,
                 "Carver Home", "l360-1"),
    DerivedPlace(SECOND, "grant_carver", "c", *_AT[SECOND], 100.0, "home", 1.0,
                 None, None),
    DerivedPlace(GYM, "grant_carver", "c", *_AT[GYM], 100.0, "unlabeled", 0.0,
                 None, None),
]


class _FakeMobility:
    def __init__(self, stays=None, places=None, fail=False):
        self.stays = stays or []
        self.places = places if places is not None else PLACES
        self.fail = fail
        self.calls: list[tuple] = []

    async def stays_for_subject(self, *, subject_id):
        self.calls.append(("stays", subject_id))
        if self.fail:
            raise RuntimeError("db down")
        return list(self.stays)

    async def places_for_subject(self, *, subject_id):
        self.calls.append(("places", subject_id))
        return list(self.places)


def _tool(persons, mobility, *, members=(MILES, GRANT), fail=False, tagger=None,
          location_trusted=False, metrics=None, with_pat=False):
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=_room(persons, members=members, fail=fail, tagger=tagger),
        transit_source=None,
        presence_circles=(_circle(persons, with_pat=with_pat),),
        location_trusted=location_trusted, mobility=mobility,
        clock=lambda: NOW.astimezone(UTC),
    )
    return next(s for s in specs if s.name == "routine_read").call


WEDNESDAY = "2026-09-09"


def _no_coordinates(value) -> bool:
    if isinstance(value, dict):
        return all(k not in ("lat", "lng") and _no_coordinates(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_no_coordinates(v) for v in value)
    return True


async def test_where_will_grant_be_on_wednesday_night(persons):
    tool = _tool(persons, _FakeMobility(stays=_history(6)))
    out = await tool(WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "ok"
    assert out["window"] == {
        "from_iso": "2026-09-09T22:00:00+08:00", "to_iso": "2026-09-10T06:00:00+08:00",
    }
    [grant] = out["people"]
    assert grant["person"] == "Grant Carver"
    # A forecast said the way a friend would (design/27 §4.3, amended
    # 2026-09-28): the hedge is in the words, and no number reaches her.
    assert grant["routine"] == "Wednesdays 22:00-06:00: should be at Carver Home"
    assert grant["usually_at"] == "Carver Home"
    assert set(grant) == {"person", "routine", "usually_at", "also"}
    assert "forecast" in out["note"]
    for word in ("will be", "can't", "cannot", "not where"):
        assert word not in out["note"]  # nothing that reads as a reason to disclaim
    assert _no_coordinates(out)  # R-28: nothing here can be read aloud as a position


async def test_tuesday_night_is_the_second_home_said_as_one_even_unnamed(persons):
    tool = _tool(persons, _FakeMobility(stays=_history(6)))
    out = await tool("2026-09-08", 23, 5, name="Chi")
    person = out["people"][0]
    assert person["usually_at"] == "a home of theirs"


async def test_thin_history_is_said_inside_the_routine_as_rough(persons):
    tool = _tool(persons, _FakeMobility(stays=_history(1)))
    out = await tool(WEDNESDAY, 22, 6, name="Chi")
    # One week of history, read roughly from other hours: said one step less
    # surely, and the reason is not recited.
    assert out["status"] == "ok"
    assert out["people"][0]["routine"] == (
        "Wednesdays 22:00-06:00: maybe at Carver Home, maybe at a home of theirs")


async def test_no_history_is_nothing_found_with_its_window(persons):
    tool = _tool(persons, _FakeMobility(stays=[]))
    out = await tool(WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "nothing_found"
    assert out["note"] and out["window"]["from_iso"].startswith("2026-09-09T22")


async def test_unwired_is_unavailable_not_nothing_found(persons):
    tool = _tool(persons, None)
    out = await tool(WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "unavailable" and out["note"]


@pytest.mark.parametrize("date,from_h,to_h", [
    ("next wednesday", 22, 6), ("2026-09-09", 24, 6), ("2026-09-09", 22, -1),
])
async def test_a_bad_window_is_unavailable_and_reads_nothing(persons, date, from_h, to_h):
    mobility = _FakeMobility(stays=_history(6))
    out = await _tool(persons, mobility)(date, from_h, to_h, name="Chi")
    assert out["status"] == "unavailable" and out["note"]
    assert mobility.calls == []


async def test_a_store_failure_is_unavailable(persons):
    out = await _tool(persons, _FakeMobility(fail=True))(WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "unavailable"
    assert "not that they have no routine" in out["note"]


# ---- the guard is where_been's: same matrix, no read before it passes -------


async def test_a_stranger_in_the_room_is_refused_before_any_read(persons):
    mobility = _FakeMobility(stays=_history(6))
    metrics = Metrics()
    out = await _tool(persons, mobility, members=(MILES, GRANT, STRANGER),
                      metrics=metrics)(WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "refused" and out["note"]
    assert mobility.calls == []
    assert _labelled(metrics, "nora_presence_disclosure_refusals_total", "reason", "not_subset")


async def test_an_unnamed_account_is_refused_unless_the_room_is_trusted(persons):
    mobility = _FakeMobility(stays=_history(6))
    out = await _tool(persons, mobility, members=(MILES, UNNAMED))(
        WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "refused" and mobility.calls == []
    metrics = Metrics()
    trusted = await _tool(persons, mobility, members=(MILES, UNNAMED),
                          location_trusted=True, metrics=metrics)(
        WEDNESDAY, 22, 6, name="Chi")
    assert trusted["status"] == "ok"
    assert _labelled(metrics, "nora_presence_disclosure_waived_total", "reason", "unnamed")


async def test_a_degraded_room_is_unavailable_not_refused(persons):
    mobility = _FakeMobility(stays=_history(6))
    tagger = persons.resolve("signal", "+85290000101")
    out = await _tool(persons, mobility, members=(MILES,), fail=True, tagger=tagger)(
        WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "unavailable" and mobility.calls == []


async def test_not_found_and_ambiguous(persons):
    mobility = _FakeMobility(stays=_history(6))
    out = await _tool(persons, mobility)(WEDNESDAY, 22, 6, name="Nobody")
    assert out["status"] == "not_found" and out["note"]
    amb = await _tool(persons, mobility, with_pat=True)(WEDNESDAY, 22, 6, name="Miles")
    assert amb["status"] == "ambiguous" and amb["note"] and len(amb["could_be"]) == 2
    assert mobility.calls == []


# ---- independent review, 2026-09-28 -----------------------------------------


async def test_a_person_known_only_to_a_later_circle_is_still_answered(persons):
    """The fold is `where_been`'s: every circle is tried for `ok` first. Pat is
    on circle B only; circle A's `not_found` must not end the call."""
    from nora.agent.whereis import Circle

    def circle(cid, ids):
        return Circle(circle_id=cid, person_ids=frozenset(ids),
                      people=tuple(persons.by_person[i] for i in ids))

    circles = (
        circle("A", ("grant_carver", "miles_novak")),
        circle("B", ("grant_carver", "miles_novak", "pat_ho")),
    )
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, GRANT)), transit_source=None,
        presence_circles=circles, location_trusted=False,
        mobility=_FakeMobility(stays=_history(6)),
        clock=lambda: NOW.astimezone(UTC),
    )
    tool = next(s for s in specs if s.name == "routine_read").call
    out = await tool(WEDNESDAY, 22, 6, name="Pat")
    assert out["status"] == "ok"


async def test_a_mostly_unobserved_window_says_the_record_is_patchy(persons):
    """Wednesdays that only observed 22:00-23:00 of an 8-hour window: always
    home in that hour, but a patchy record says it one step less surely."""
    stays = [
        _stay(datetime(2026, 8, 5, tzinfo=HK) + timedelta(weeks=w), 22, 23, HOME)
        for w in range(4)
    ]
    out = await _tool(persons, _FakeMobility(stays=stays))(WEDNESDAY, 22, 6, name="Chi")
    [grant] = out["people"]
    assert grant["routine"] == "Wednesdays 22:00-06:00: probably at Carver Home"


async def test_non_ambiguous_answers_carry_no_could_be(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(WEDNESDAY, 22, 6, name="Nobody")
    assert out["status"] == "not_found" and "could_be" not in out


async def test_a_second_place_is_sometimes_never_no_usual_place(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))("2026-09-09", 19, 22, name="Chi")
    person = out["people"][0]
    # Gym 19-21 then home: the gym is the probable one, home is named too.
    assert person["routine"] == "Wednesdays 19:00-22:00: probably at a place they go"
    assert person["also"] == ["Carver Home"]


# ---- independent review of the reshaping, 2026-09-28 ------------------------


def _wednesdays(segments, weeks=6):
    """`segments` = [(start_h, end_h, place_id)], repeated every Wednesday
    for `weeks` weeks before NOW."""
    first = datetime(2026, 8, 26, tzinfo=HK) - timedelta(weeks=weeks - 1)
    return [
        _stay(first + timedelta(weeks=w), a, b, pid)
        for w in range(weeks) for a, b, pid in segments
    ]


async def test_with_no_usual_place_the_most_frequent_is_still_named(persons):
    stays = _wednesdays([(19, 20, GYM), (20, 21, HOME), (21, 22, SECOND)])
    out = await _tool(persons, _FakeMobility(stays=stays))(WEDNESDAY, 19, 22, name="Chi")
    [p] = out["people"]
    assert p["usually_at"] is None
    assert p["routine"].startswith("Wednesdays 19:00-22:00: hard to say; sometimes ")
    for name in ("Carver Home", "a home of theirs", "a place they go"):
        assert name in p["routine"]


async def test_no_number_reaches_her(persons):
    """A percent, share, week or day count in her input came out in her
    reply (live, 2026-09-28: «過去七個禮拜 100%»). None is in the result."""
    for window in ((WEDNESDAY, 22, 6), (WEDNESDAY, 19, 22), ("2026-09-08", 19, 23)):
        out = await _tool(persons, _FakeMobility(stays=_history(6)))(*window, name="Chi")
        for person in out["people"]:
            assert not re.search(r"\d+ ?%|percent|share|week|\bdays?\b", str(person))
    arrival = await _tool(persons, _FakeMobility(stays=_history(6)))(
        WEDNESDAY, 16, 4, name="Chi", what="arrival")
    assert not re.search(r"\d+ of \d+|percent|\bdays\b|habits|evidence",
                         str(arrival["people"]))


async def test_their_own_small_places_are_pooled_and_never_the_answer(persons):
    from nora.domain.mobility import DerivedPlace

    small = [
        DerivedPlace(100 + i, "grant_carver", "c", 22.2 + i / 1000, 114.1, 100.0,
                     "unlabeled", 0.0, f"Shop {i}", None)
        for i in range(20)
    ]
    _AT.update({100 + i: (22.2 + i / 1000, 114.1) for i in range(20)})
    segments = [(19, 20, HOME)] + [
        (20 + i * 0.15, 20 + i * 0.15 + 0.15, 100 + i) for i in range(20)
    ]
    out = await _tool(persons, _FakeMobility(stays=_wednesdays(segments),
                                             places=PLACES + small))(WEDNESDAY, 19, 23, name="Chi")
    [p] = out["people"]
    # The pool of small places is never the answer, and never named.
    assert p["usually_at"] in (None, "Carver Home")
    assert "other places of theirs" not in str(p)
    assert "not one of their places" not in str(p)  # every stop was one of theirs


async def test_the_ladder_reads_probably_between_60_and_85(persons):
    stays = _wednesdays([(19, 21.1, HOME), (21.1, 22, GYM)])  # 70% home
    out = await _tool(persons, _FakeMobility(stays=stays))(WEDNESDAY, 19, 22, name="Chi")
    [p] = out["people"]
    assert p["routine"] == "Wednesdays 19:00-22:00: probably at Carver Home"
    assert (p["usually_at"], p["also"]) == ("Carver Home", ["a place they go"])


# ---- the movement model's views (design/27 §4.4 M1, M3, M4) ---------------

from types import SimpleNamespace  # noqa: E402


class _FakeFixes:
    def __init__(self, at: tuple[float, float] | None, age_min: float = 1):
        self.at, self.age_min = at, age_min

    async def latest_fix(self, *, circle_id, person_id):
        if self.at is None:
            return None
        ts = int(NOW.timestamp() * 1000 - self.age_min * 60_000)
        return SimpleNamespace(ts=ts, lat=self.at[0], lng=self.at[1])


def _tool_live(persons, mobility, fixes):
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, GRANT)), transit_source=None,
        presence_circles=(_circle(persons),), location_trusted=False,
        mobility=mobility, clock=lambda: NOW.astimezone(UTC), presence_source=fixes,
    )
    return next(s for s in specs if s.name == "routine_read").call


async def test_when_is_he_usually_back_home_on_wednesday_night(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(
        WEDNESDAY, 16, 4, name="Chi", what="arrival")
    assert out["status"] == "ok"
    [p] = out["people"]
    # Two homes (one unnamed): «home» is either, and the answer says so
    # instead of borrowing one home's name for both (review 2026-09-28).
    assert (p["what"], p["place"]) == ("arrival", "one of their homes")
    # Wednesdays in the fixture: gym 19-21, home from 21:30, every week.
    assert p["routine"] == "Wednesdays: should get to one of their homes around 21:30"
    assert p["places"] == ["a home of theirs", "Carver Home"]


async def test_a_named_place_is_that_place(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(
        WEDNESDAY, 16, 4, name="Chi", what="arrival", place="Carver Home")
    [p] = out["people"]
    assert p["place"] == "Carver Home" and "place_ref" not in p
    assert p["routine"] == "Wednesdays: should get to Carver Home around 21:30"


async def test_a_window_from_midnight_reads_the_whole_day(persons):
    """0 to 23 answered «not enough history» against a 04:00 rollover."""
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(
        WEDNESDAY, 0, 23, name="Chi", what="arrival", place="Carver Home")
    assert out["status"] == "ok"
    assert "should get to Carver Home around 21:30" in out["people"][0]["routine"]


async def test_when_does_he_leave_a_named_place(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(
        WEDNESDAY, 16, 23, name="Chi", what="departure", place="gym")
    # «gym» matches no label or kind: they have no such place, which is not
    # the same as a thin history (review 2026-09-28).
    assert out["status"] == "nothing_found"
    assert "don't know a place of theirs" in out["note"]
    assert "history" not in out["note"]


async def test_already_home_today_is_said_as_done(persons):
    saturday = NOW.date().isoformat()
    out = await _tool_live(persons, _FakeMobility(stays=_history(6)), _FakeFixes(_AT[HOME]))(
        saturday, 10, 4, name="Chi", what="arrival")
    [p] = out["people"]
    assert p["today"].startswith("already at one of their homes")


async def test_a_stale_reading_is_not_used_for_today(persons):
    saturday = NOW.date().isoformat()
    out = await _tool_live(
        persons, _FakeMobility(stays=_history(6)), _FakeFixes(_AT[HOME], age_min=90),
    )(saturday, 10, 4, name="Chi", what="arrival")
    # No fresh reading: today is not said at all, not «not yet» (R-5).
    assert "today" not in out["people"][0]


async def test_today_against_the_routine_names_where_they_usually_are(persons):
    saturday = NOW.date().isoformat()
    out = await _tool_live(persons, _FakeMobility(stays=_history(6)), _FakeFixes(_AT[GYM]))(
        saturday, 12, 14, name="Chi")
    [p] = out["people"]
    assert p["today"].startswith("now a place they go")
    assert "usually at Carver Home" in p["today"]


async def test_no_result_carries_a_handle(persons):
    """Places are named in words `route` takes back this turn, never an id
    (DEC-216 M3, amended: an id in a result is a low-level identifier)."""
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(WEDNESDAY, 22, 6, name="Chi")
    assert "ref" not in str(out) and "place:" not in str(out)


async def test_two_unnamed_homes_of_one_person_read_as_two_places(persons):
    from nora.domain.mobility import DerivedPlace

    unnamed = [
        DerivedPlace(HOME, "grant_carver", "c", *_AT[HOME], 100.0, "home", 1.0, None, None),
        DerivedPlace(SECOND, "grant_carver", "c", *_AT[SECOND], 100.0, "home", 1.0,
                     None, None),
    ]
    stays = _wednesdays([(19, 21.9, HOME), (21.9, 23, SECOND)])  # ~72% / 28%
    out = await _tool(persons, _FakeMobility(stays=stays, places=unnamed))(
        WEDNESDAY, 19, 23, name="Chi")
    [p] = out["people"]
    assert (p["usually_at"], p["also"]) == ("a home of theirs", ["a home of theirs (2)"])


async def test_tonight_is_still_tonight_after_midnight(persons):
    """A window from Friday 20:00 to Saturday 14:00, asked Saturday noon: the
    calendar date differs, the window holds now, so today is said."""
    out = await _tool_live(persons, _FakeMobility(stays=_history(6)), _FakeFixes(_AT[GYM]))(
        "2026-09-04", 20, 14, name="Chi")
    assert out["people"][0]["today"].startswith("now a place they go")


# ---- evidence on request (operator 2026-09-28) -------------------------------


async def test_evidence_only_when_asked_and_as_counts_of_the_past(persons):
    """«How sure are you?» gets what the past weeks were; the first answer
    carries no number at all."""
    tool = _tool(persons, _FakeMobility(stays=_history(6)))
    plain = await tool(WEDNESDAY, 22, 6, name="Chi")
    assert "evidence" not in plain["people"][0] and "evidence" not in plain["note"]
    asked = await tool(WEDNESDAY, 22, 6, name="Chi", detail="evidence")
    [p] = asked["people"]
    assert p["routine"] == plain["people"][0]["routine"]
    assert p["evidence"].startswith("Over the last 6 Wednesdays at 22:00-06:00")
    assert "Carver Home" in p["evidence"] and "%" in p["evidence"]
    assert "not a confidence figure" in asked["note"]
    assert "confiden" not in p["evidence"]  # a count of the past, never a confidence


async def test_arrival_evidence_counts_the_days(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(
        WEDNESDAY, 16, 4, name="Chi", what="arrival", place="Carver Home",
        detail="evidence")
    assert out["people"][0]["evidence"] == (
        "Of the last 6 Wednesdays they were seen: got there around 21:30 on 6.")


async def test_two_kinds_of_night_are_said_as_probably_though_some_nights(persons):
    stays = []
    first = datetime(2026, 8, 26, tzinfo=HK) - timedelta(weeks=5)
    for w in range(6):
        wed = first + timedelta(weeks=w)
        stays.append(_stay(wed, 9, 18, GYM))
        stays.append(_stay(wed, 23.75, 31, HOME) if w in (1, 3) else _stay(wed, 19.5, 31, HOME))
    out = await _tool(persons, _FakeMobility(stays=stays))(
        WEDNESDAY, 16, 4, name="Chi", what="arrival", place="Carver Home")
    assert out["people"][0]["routine"] == (
        "Wednesdays: probably gets to Carver Home around 19:30, "
        "though some nights not till around 23:45")
