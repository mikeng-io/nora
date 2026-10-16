"""`route` takes back a place a presence read NAMED this turn: «a home of
theirs» becomes routable with no handle in any result, resolved server side
behind the SAME R-31 guard as the read that named it, and its
coordinate never reaches the model, the reply or a link."""

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
    person = (await read(TUESDAY, 19, 23, name="Chi"))["people"][0]
    assert person["usually_at"] == "a home of theirs"
    assert "ref" not in str(person) and "place:" not in str(person)  # no handle
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


# ---- name resolution edge cases ----------------------------


async def test_a_place_is_bound_only_for_an_answer_she_is_shown(persons):
    """An arrival read with too little history answers nothing_found: the
    place it looked at must not become routable."""
    routes = _Routes()
    mobility = _Mobility()
    mobility.stays = []
    read, route = _turn(persons, routes, mobility)
    out = await read(TUESDAY, 16, 4, name="Chi", what="arrival", place="Carver Home")
    assert out["status"] == "nothing_found"
    await route(origin="Carver Home", destination="Olympian City")
    assert all("carver home" not in (c["local_places"] or {}) for c in routes.calls)


async def test_the_words_are_matched_without_case_and_echoed_as_she_gave_them(persons):
    routes = _Routes()
    read, route = _turn(persons, routes, _Mobility())
    await read(TUESDAY, 19, 23, name="Chi")
    out = await route(origin="A Home Of Theirs", destination="Olympian City")
    assert out["status"] == "ok" and out["origin"] == "A Home Of Theirs"
    assert (routes.calls[0]["local_places"]["a home of theirs"].lat,) == (_AT[SECOND][0],)


def _where_been_turn(persons, routes, mobility):
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, GRANT)), transit_source=None,
        presence_circles=(_circle(persons),), location_trusted=False,
        mobility=mobility, route_source=routes, clock=lambda: NOW.astimezone(UTC),
    )
    by = {s.name: s.call for s in specs}
    return by["where_been"], by["route"]


async def test_a_place_where_been_named_is_routable_by_that_name(persons):
    class _Places(_Mobility):
        async def places_with_aggregates(self, *, subject_id):
            return [{
                "id": SECOND, "kind": "home", "label": None, "saved": False,
                "confidence": 1.0, "first_seen_ts": 0, "last_seen_ts": 0,
                "stay_count": 3, "total_dwell_ms": 1,
            }]

    routes = _Routes()
    been, route = _where_been_turn(persons, routes, _Places())
    out = await been("places", name="Chi")
    assert out["people"][0]["places"][0]["name"] == "a home of theirs"
    assert (await route(origin="a home of theirs", destination="Olympian City"))["status"] == "ok"
    assert routes.calls[0]["local_places"]["a home of theirs"].lng == _AT[SECOND][1]


async def test_a_saved_place_wins_over_a_bound_name_of_the_same_words(persons):
    """Someone's derived place is labelled «Home»; the household's own saved
    «Home» is what route resolves."""
    from nora.domain.presence import SavedPlace

    class _Presence:
        async def places_for(self, *, circle_id):
            return [SavedPlace(circle_id=circle_id, place_id="h", name="Home",
                               lat=22.11, lng=114.22, updated_ms=0, radius_m=50.0)]

        async def latest_fix(self, *, circle_id, person_id):
            return None

    labelled = DerivedPlace(SECOND, "grant_carver", "c", *_AT[SECOND], 100.0, "home",
                            1.0, "Home", "l360-home")
    routes = _Routes()
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, GRANT)), transit_source=None,
        presence_circles=(_circle(persons),), location_trusted=False,
        mobility=_Mobility(places=[labelled], by_id={SECOND: labelled}),
        route_source=routes, presence_source=_Presence(),
        clock=lambda: NOW.astimezone(UTC),
    )
    by = {s.name: s.call for s in specs}
    await by["routine_read"](TUESDAY, 19, 23, name="Chi")
    out = await by["route"](origin="Home", destination="Olympian City")
    assert out["status"] == "ok"
    home = routes.calls[0]["local_places"]["home"]
    assert (home.lat, home.lng) == (22.11, 114.22)
    assert "maps_url" in out  # a saved name is words anyone may open


async def test_two_peoples_places_with_the_same_label_stay_apart(persons):
    """Both labelled «Home» (capitalised, so the turn's lookup must fold case
    the way it stores): the second is qualified by whose it is."""

    class _Labelled(_TwoPeople):
        def __init__(self):
            super().__init__()
            self.homes = {
                k: DerivedPlace(p.id, p.subject_id, p.circle_id, p.lat, p.lng, p.radius_m,
                                p.kind, p.confidence, "Home", f"l360-{p.id}")
                for k, p in self.homes.items()
            }
            self.by_id = {p.id: p for p in self.homes.values()}

    routes = _Routes()
    read, _ = _turn(persons, routes, _Labelled())
    out = await read(TUESDAY, 19, 23)
    names = sorted(p["usually_at"] for p in out["people"])
    assert names[0] == "Home" and names[1] in ("Home (Grant)", "Home (Miles)")
