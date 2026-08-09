"""F4: the canned eval backends and the presence subset-gating they rely on.

These pin that `build_eval_tool_registry`'s providers actually answer `ok` (not
`unavailable`) — the whole point of removing the routing gate's fallback confound
— and, for the fiddly one, that the eval `Circle` + a matching room clear the
R-31 disclosure gate so `saved_places`/`where_is` disclose.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from nora.agent.room import Room
from nora.agent.whereis import Circle, resolvable_places
from nora.persons import load_persons
from nora.testing import (
    EVAL_CIRCLE_ID,
    CannedPresenceReader,
    CannedRouteSource,
    CannedSearchProvider,
    CannedTransitSource,
    CannedWeatherProvider,
    FakeRosterSource,
)

_IDENTITY = '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'


def _persons():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "identity.toml"
        path.write_text(_IDENTITY)
        return load_persons(path)


async def test_canned_weather_answers_ok():
    obs = await CannedWeatherProvider().current(place="Hong Kong")
    assert obs is not None
    assert obs.temperatures  # non-empty → the weather tool renders a real answer


async def test_canned_search_answers_ok():
    out = await CannedSearchProvider().search(query="anything")
    assert out["status"] == "ok"
    assert out["results"]


async def test_canned_place_and_transit_answer_ok():
    place = await CannedRouteSource().place_info("Festival Walk")
    assert place["name"] == "Festival Walk" and place.get("open_now") is True
    arr = await CannedTransitSource().arrivals("K73")
    assert arr["status"] == "ok"


async def test_presence_subset_gate_discloses_the_saved_place():
    """The crux of F4's presence backing: the eval room ({miles}) is a subset of the
    eval circle ({miles}), so `resolvable_places` — the exact logic `saved_places`
    runs — PERMITS and returns the canned «返工» place. If the roster/circle subset
    ever breaks, this returns empty and reddens (rather than silently degrading
    saved_places to unavailable and making the gate's 'hard' promotion a lie)."""
    persons = _persons()
    room = Room(
        platform="signal", conversation_id="g1",
        roster_source=FakeRosterSource(groups={"g1": (frozenset({"+852111"}),)}),
        persons=persons, self_ids=frozenset({"+85200000000"}), tagger=None,
    )
    view = await room.scope()
    assert view.available and not view.degraded and view.unnamed == 0

    circle = Circle(circle_id=EVAL_CIRCLE_ID, person_ids=frozenset({"miles"}))
    places = await resolvable_places(room=room, circles=(circle,), presence=CannedPresenceReader())
    assert [p.name for p in places] == ["返工"]
