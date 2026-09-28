"""`routine_read` (design/proposed/27-presence-analytics.md §4.3, P1b).

«Where will Grant be on Wednesday night?» answered from the Tier 4
forecast, behind `where_been`'s own guard (`_presence_audience`). The legs:
every status with its note; the guard matrix (refused, unnamed, degraded,
waiver counted, not_found, ambiguous) with NO store read before it passes; the
answer carrying its evidence (window, basis, weeks, observed share) and no
coordinate (R-28); and a weak basis told as a rough guess.
"""

from __future__ import annotations

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
    # Comprehended, not reported (design/27 §4.3, amended 2026-09-28): the
    # routine arrives worked out; the numbers are evidence.
    assert grant["routine"] == "Wednesdays 22:00-06:00: almost always at Carver Home"
    assert (grant["usually_at"], grant["how_often"]) == ("Carver Home", "almost always")
    assert grant["evidence"]["weeks"] == 6 and grant["evidence"]["basis"] == "hour"
    assert grant["evidence"]["shares"][0]["percent"] >= 95
    assert sum(x["percent"] for x in grant["evidence"]["shares"]) == 100
    assert "habit" in out["note"]
    for word in ("will be", "can't", "cannot", "not where"):
        assert word not in out["note"]  # nothing that reads as a reason to disclaim
    assert _no_coordinates(out)  # R-28: nothing here can be read aloud as a position


async def test_tuesday_night_is_the_second_home_said_as_one_even_unnamed(persons):
    tool = _tool(persons, _FakeMobility(stays=_history(6)))
    out = await tool("2026-09-08", 23, 5, name="Chi")
    person = out["people"][0]
    assert person["usually_at"] == "a home of theirs"
    assert person["evidence"]["shares"][0]["kind"] == "home"


async def test_thin_history_is_said_inside_the_routine_as_rough(persons):
    tool = _tool(persons, _FakeMobility(stays=_history(1)))
    out = await tool(WEDNESDAY, 22, 6, name="Chi")
    assert out["status"] == "ok"
    assert out["people"][0]["evidence"]["basis"] != "hour"
    assert "roughly" in out["people"][0]["routine"]


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
    """Six Wednesdays that only observed 22:00-23:00 of an 8-hour window: the
    shares are of that hour, and the note says what observed_share means."""
    stays = [
        _stay(datetime(2026, 8, 5, tzinfo=HK) + timedelta(weeks=w), 22, 23, HOME)
        for w in range(4)
    ]
    out = await _tool(persons, _FakeMobility(stays=stays))(WEDNESDAY, 22, 6, name="Chi")
    [grant] = out["people"]
    assert grant["evidence"]["record"] == "patchy"
    assert "percent" not in str({k: v for k, v in grant["evidence"].items() if k != "shares"})
    assert "on a patchy record" in grant["routine"]


async def test_non_ambiguous_answers_carry_no_could_be(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))(WEDNESDAY, 22, 6, name="Nobody")
    assert out["status"] == "not_found" and "could_be" not in out


async def test_a_second_place_is_sometimes_never_no_usual_place(persons):
    out = await _tool(persons, _FakeMobility(stays=_history(6)))("2026-09-09", 19, 22, name="Chi")
    also = out["people"][0]["also"]
    assert also and all(a["how_often"] != "no single usual place" for a in also)
