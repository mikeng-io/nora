"""A movement anomaly's line says where the stay was and what is usual for the
person at that place, when the presence store holds them (DEC-279).

Without the stay's place and the person's usual pattern the session has nothing
to judge. The stay, its place and the person's own past are in the presence
store; the line reads them at grounding time (`stay_context_reader`),
recomputing the deviations the way the rollup scored them. Stays and places here
are invented.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent.room import RoomView
from nora.agent.whereis import Circle
from nora.app.cognition_grounding import build_grounder, place_release, stay_context_reader
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import Room, build_situation
from nora.domain.mobility import DerivedPlace, Stay
from nora.domain.models import PersonRef
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import render_agenda
from nora.persons import Persons
from tests.unit.cognition_view import items, rows
from tests.unit.test_cognition_grounding import _lineage

HK = ZoneInfo("Asia/Hong_Kong")
NOW = int(dt.datetime(2026, 9, 30, 15, 30, tzinfo=HK).timestamp() * 1000)
HOME = Room("signal", "room-a")
PERSONS = Persons(
    by_person={"ben": PersonRef(person_id="ben", name="Ben", honcho_peer_id="ben")},
    index={("signal", "+852000"): "ben"},
)
GYM = DerivedPlace(
    id=1, subject_id="ben", circle_id="c1", lat=22.3, lng=114.2, radius_m=80.0,
    kind="unlabeled", confidence=0.9, label="Harbour Gym", saved_place_id="sp1",
)


def _ms(day: int, hour: int) -> int:
    return int(dt.datetime(2026, 9, day, hour, 0, tzinfo=HK).timestamp() * 1000)


def _stay(day: int, start_hour: int, end_hour: int, place_id: int | None = 1) -> Stay:
    return Stay(
        subject_id="ben", person_id="ben", circle_id="c1", started_ts=_ms(day, start_hour),
        ended_ts=_ms(day, end_hour), lat=22.3, lng=114.2, radius_m=50.0, fix_count=20,
        place_id=place_id,
    )


#: Six mornings of an hour at the gym, then one five-hour stay there.
HISTORY = [_stay(d, 8, 9) for d in range(22, 28)]
ODD = _stay(29, 8, 13)


class _Mobility:
    def __init__(self, stays: Sequence[Stay], places: Sequence[DerivedPlace], *,
                 fail: bool = False) -> None:
        self.stays, self.places, self.fail = list(stays), list(places), fail

    async def stays_for_subject(self, *, subject_id: str) -> list[Stay]:
        if self.fail:
            raise ConnectionError("presence store down")
        return [s for s in self.stays if s.subject_id == subject_id]

    async def places_for_subject(self, *, subject_id: str) -> list[DerivedPlace]:
        return [p for p in self.places if p.subject_id == subject_id]


BEN = PERSONS.by_person["ben"]
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
OUTSIDER = PersonRef(person_id="zed", name="Zed", honcho_peer_id="zed")
CIRCLE = Circle(circle_id="c1", person_ids=frozenset({"ben", "ada"}))


def _release(*rooms: RoomView, fail: bool = False, trusted: bool = False) -> Any:
    """The unit's rooms as `presence_audience` reads them, one view each."""
    views = {f"room-{i}": v for i, v in enumerate(rooms)}

    async def view(platform: str, conversation_id: str) -> RoomView:
        if fail:
            raise ConnectionError("roster daemon down")
        return views[conversation_id]

    return place_release(
        rooms_of=lambda unit: [("signal", cid, trusted) for cid in views],
        room_view=view, circles=(CIRCLE,),
    )


ALL_CIRCLE = _release(RoomView(available=True, people=(BEN, ADA)))


def _rollup(stay: Stay) -> Stimulus:
    return Stimulus(
        id=1, unit="u", kind="rollup", item="deviation_anomaly",
        ref=f"deviation|anomaly|ben|{stay.started_ts}", at_ms=stay.ended_ts,
        recorded_at_ms=stay.ended_ts,
    )


async def _none(*_a: Any) -> Sequence[Any]:
    return ()


async def _ground(
    mobility: _Mobility | None, stimulus: Stimulus, release: Any = ALL_CIRCLE,
) -> tuple[Grounding, str]:
    grounder = build_grounder(
        records=None, persons=PERSONS, media_join=None, note_index=_none, events_in=_none,
        lineage=_lineage(), clock=lambda: NOW / 1000, unit_rooms=lambda unit: set(),
        stay_context=None if mobility is None else stay_context_reader(
            mobility, tz_name="Asia/Hong_Kong", min_samples=5, z_threshold=3.0,
            novelty_radius_m=100.0,
        ),
        place_release=release,
    )
    sit = build_situation(unit="u", pending=[stimulus], home_room=HOME, now_ms=NOW,
                          last_session_ms=None)
    grounding = await grounder("u", sit.agenda.items, [stimulus])
    prompt = render_agenda(sit.agenda.items, stimuli={1: stimulus}, grounding=grounding,
                           now_ms=NOW, last_session_ms=None, tz=HK)
    (item,) = items(prompt)
    (row,) = rows(item)
    return grounding, row.text or ""


async def test_an_anomaly_line_says_where_the_stay_was_and_what_is_usual_there():
    _, line = await _ground(_Mobility([*HISTORY, ODD], [GYM]), _rollup(ODD))
    assert line.startswith("Ben: a stay the rhythm scored anomalous at Harbour Gym, from ")
    assert "stayed 300 min, where their stays there usually last about 60 min" in line
    assert "left at 13:00, where they usually leave about 09:00" in line


async def test_an_unsaved_place_is_named_by_what_it_is_to_them_never_as_a_street():
    unsaved = dataclasses.replace(GYM, label="Some Road", saved_place_id=None)
    _, line = await _ground(_Mobility([*HISTORY, ODD], [unsaved]), _rollup(ODD))
    assert " at a place they go near Some Road, " in line


async def test_the_session_row_keeps_the_deviation_kinds_and_never_the_place_words():
    grounding, _ = await _ground(_Mobility([*HISTORY, ODD], [GYM]), _rollup(ODD))
    (kept,) = grounding.as_dict()["rollups"]
    assert sorted(kept["deviations"]) == ["departure", "duration"]
    assert "Harbour Gym" not in repr(grounding.as_dict())


async def test_nothing_is_invented_when_the_store_does_not_hold_the_stay_or_its_place():
    plain = "Ben: a stay the rhythm scored anomalous, from "
    _, no_stay = await _ground(_Mobility(HISTORY, [GYM]), _rollup(ODD))
    assert no_stay.startswith(plain) and ";" not in no_stay
    placeless = _stay(29, 8, 13, place_id=None)
    _, no_place = await _ground(_Mobility([*HISTORY, placeless], [GYM]), _rollup(placeless))
    assert no_place.startswith(plain)


async def test_a_presence_read_that_fails_leaves_the_line_as_it_was():
    _, unwired = await _ground(None, _rollup(ODD))
    _, failed = await _ground(_Mobility([*HISTORY, ODD], [GYM], fail=True), _rollup(ODD))
    assert failed == unwired
    assert unwired.startswith("Ben: a stay the rhythm scored anomalous, from ")


# ---- a place is named only where `where_been` would name it (R-35) ----

PLAIN = "Ben: a stay the rhythm scored anomalous, from "


async def test_a_unit_with_someone_outside_the_circle_hears_no_place():
    """One room of the unit holds a person outside Ben's location circle: the
    line says how the stay differed and never where, no saved name and no
    street, though the second room alone would pass."""
    release = _release(
        RoomView(available=True, people=(BEN, ADA)),
        RoomView(available=True, people=(BEN, OUTSIDER)),
    )
    _, line = await _ground(_Mobility([*HISTORY, ODD], [GYM]), _rollup(ODD), release)
    assert line.startswith(PLAIN)
    assert "Harbour Gym" not in line and " near " not in line
    assert "stayed 300 min, where their stays there usually last about 60 min" in line


async def test_an_unmapped_account_in_a_room_withholds_the_place():
    release = _release(RoomView(available=True, people=(BEN, ADA), unnamed=1))
    _, line = await _ground(_Mobility([*HISTORY, ODD], [GYM]), _rollup(ODD), release)
    assert line.startswith(PLAIN) and "Harbour Gym" not in line


async def test_a_location_trusted_unit_names_the_place_as_where_been_does():
    release = _release(RoomView(available=True, people=(BEN, OUTSIDER)), trusted=True)
    _, line = await _ground(_Mobility([*HISTORY, ODD], [GYM]), _rollup(ODD), release)
    assert " at Harbour Gym, " in line


async def test_a_guard_that_cannot_read_a_room_names_no_place():
    for release in (
        _release(RoomView(available=True, people=(BEN,)), fail=True),
        _release(RoomView(available=False)),
        _release(RoomView(available=True, people=(BEN,), degraded=True)),
        None,
    ):
        _, line = await _ground(_Mobility([*HISTORY, ODD], [GYM]), _rollup(ODD), release)
        assert line.startswith(PLAIN) and "Harbour Gym" not in line


async def test_the_usual_place_is_withheld_with_the_stay_place():
    """`unusual_place` names where they usually are: withheld the same way."""
    from nora.app.cognition_grounding import _withheld
    from nora.domain.cognition_ground import StayDeviation

    (d,) = _withheld((StayDeviation(kind="unusual_place", usual_place="Mum's flat"),))
    assert d.usual_place is None and d.kind == "unusual_place"
