"""`route` takes back a place a presence read NAMED this turn (design/27 §4.4
M3, amended 2026-09-28): «a home of theirs» becomes routable with no handle in
any result, resolved server side behind the SAME R-31 guard as the read that
named it, and its coordinate never reaches the model, the reply or a link."""

from __future__ import annotations

from datetime import UTC

import pytest

from nora.agent.tools import build_tool_specs
from nora.agent.whereis import Circle
from nora.domain.mobility import DerivedPlace
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from tests.test_where_been import GRANT, IDENTITY, MILES, _circle, _room
from tests.unit.test_routine_read import _AT, NOW, PLACES, SECOND, _FakeMobility, _history

TUESDAY = "2026-09-08"  # the fixture's second-home night


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


class _Mobility(_FakeMobility):
    def __init__(self, *, by_id=None, **kw):
        super().__init__(stays=_history(6), **kw)
        self.by_id = by_id if by_id is not None else {p.id: p for p in self.places}

    async def place_by_id(self, *, place_id):
        return self.by_id.get(place_id)


class _Routes:
    def __init__(self):
        self.calls: list[dict] = []

    async def route(self, origin, destination, **kw):
        self.calls.append({"origin": origin, "destination": destination, **kw})
        return {"status": "ok", "origin": origin, "destination": destination,
                "mode": kw.get("mode", "TRANSIT"), "duration_seconds": 1500,
                "distance_meters": 9000, "legs": [], "note": "Say the time."}


def _turn(persons, routes, mobility, *, circles=None, members=(MILES, GRANT)):
    """One turn's tools, built once, so a read's names reach `route`."""
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=members), transit_source=None,
        presence_circles=circles or (_circle(persons),), location_trusted=False,
        mobility=mobility, route_source=routes, clock=lambda: NOW.astimezone(UTC),
    )
    by = {s.name: s.call for s in specs}
    return by["routine_read"], by["route"]


async def test_a_place_the_routine_named_is_routable_by_that_name(persons):
    routes = _Routes()
    read, route = _turn(persons, routes, _Mobility())
    shares = (await read(TUESDAY, 19, 23, name="Chi"))["people"][0]["evidence"]["shares"]
    assert "a home of theirs" in {x["place"] for x in shares}
    assert all(set(x) == {"place", "kind", "percent"} for x in shares)  # no handle
    out = await route(origin="a home of theirs", destination="Olympian City")
    assert out["status"] == "ok"
    [call] = routes.calls
    local = call["local_places"]["a home of theirs"]
    assert (local.lat, local.lng) == _AT[SECOND]
    assert out["origin"] == "a home of theirs"
    assert "maps_url" not in out  # a link is built from spoken names only
    assert str(_AT[SECOND][0]) not in str(out)


async def test_a_name_no_read_gave_this_turn_is_not_resolved(persons):
    routes = _Routes()
    _, route = _turn(persons, routes, _Mobility())
    out = await route(origin="a home of theirs", destination="Olympian City")
    # Refused by the person screen, or passed on unresolved: never their home.
    assert out["status"] == "refused" or all(
        "a home of theirs" not in (c["local_places"] or {}) for c in routes.calls
    )


async def test_a_bound_place_that_no_longer_resolves_is_refused(persons):
    routes = _Routes()
    read, route = _turn(persons, routes, _Mobility(by_id={}))
    await read(TUESDAY, 19, 23, name="Chi")
    out = await route(origin="a home of theirs", destination="Olympian City")
    assert out["status"] == "refused" and out["note"] and routes.calls == []


def _circle_of(persons, circle_id, ids):
    return Circle(
        circle_id=circle_id, person_ids=frozenset(ids),
        people=tuple(persons.by_person[i] for i in ids),
    )


async def test_route_rechecks_that_the_places_person_is_in_a_passing_circle(persons):
    """Miles and Grant pass circle A; the place behind the name belongs to
    Pat, who is only in circle B, which this room is not inside. `route`
    re-checks the owner, not only the room."""
    pats = DerivedPlace(SECOND, "pat_ho", "c2", *_AT[SECOND], 100.0, "home", 1.0, None, None)
    routes = _Routes()
    circles = (
        _circle_of(persons, "a", ("grant_carver", "miles_novak")),
        _circle_of(persons, "b", ("pat_ho", "miles_novak")),
    )
    read, route = _turn(persons, routes, _Mobility(by_id={SECOND: pats}), circles=circles)
    await read(TUESDAY, 19, 23, name="Chi")
    out = await route(origin="a home of theirs", destination="Olympian City")
    assert out["status"] == "refused" and routes.calls == []


async def test_the_circle_holding_the_place_is_found_past_the_first(persons):
    routes = _Routes()
    circles = (
        _circle_of(persons, "a", ("pat_ho", "miles_novak")),
        _circle_of(persons, "b", ("grant_carver", "miles_novak")),
    )
    read, route = _turn(persons, routes, _Mobility(), circles=circles)
    await read(TUESDAY, 19, 23, name="Chi")
    out = await route(origin="a home of theirs", destination="Olympian City")
    assert out["status"] == "ok" and len(routes.calls) == 1


class _TwoPeople(_Mobility):
    """Miles's and Grant's unnamed homes: different places, the same words."""

    def __init__(self):
        mikes = DerivedPlace(99, "miles_novak", "c", 22.44, 114.02, 100.0, "home", 1.0, None, None)
        gabriels = next(p for p in PLACES if p.id == SECOND)
        super().__init__(by_id={99: mikes, SECOND: gabriels})
        self.homes = {"miles_novak": mikes, "grant_carver": gabriels}

    async def stays_for_subject(self, *, subject_id):
        home = self.homes[subject_id]
        return [s.__class__(**{**s.__dict__, "place_id": home.id, "subject_id": subject_id})
                for s in _history(6) if s.place_id == SECOND]

    async def places_for_subject(self, *, subject_id):
        return [self.homes[subject_id]]


async def test_two_peoples_unnamed_homes_get_words_that_route_to_each(persons):
    routes = _Routes()
    read, route = _turn(persons, routes, _TwoPeople())
    out = await read(TUESDAY, 19, 23)  # everyone in this conversation
    names = {p["person"]: p["usually_at"] for p in out["people"]}
    assert set(names.values()) == {"a home of theirs", "a home of theirs (Grant)"} or \
        set(names.values()) == {"a home of theirs", "a home of theirs (Miles)"}
    for person, name in names.items():
        routes.calls.clear()
        # The person screen would refuse «(Grant)»: bound words skip it.
        assert (await route(origin=name, destination="Olympian City"))["status"] == "ok"
        local = routes.calls[0]["local_places"][name.casefold()]
        expected = (22.44, 114.02) if person.startswith("Miles") else _AT[SECOND]
        assert (local.lat, local.lng) == expected
