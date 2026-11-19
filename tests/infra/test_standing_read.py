"""Select Standing from the same scoped snapshot, with real schedule publication."""

from dataclasses import replace
from datetime import timedelta

import pytest

from nora.domain.lineage import RoomFacts
from nora.domain.ticks import tick_of_at
from nora.memory.adapters.graph_clock import SqlDueSchedules
from nora.memory.adapters.graph_row_render import render_open_intention
from nora.memory.domain.presentations import standing
from tests.infra.test_retrieval_scope import (
    AT,
    BOUNDS,
    OTHER_ROOM,
    READ,
    ROOM,
    UNIT,
    references,
    write_edge,
)
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.infra.test_situation_staleness import intention, refuse_count, serves

scenario = scope_scenario
NOW = AT + timedelta(hours=3)
ROOM_KEY = f"signal:{ROOM}"
ROOM_ENTITY = "room-pointer"


def _time_trigger(at, recurrence=None):
    return {"kind": "time", "at": at.isoformat(), "zone": "UTC", "recurrence": recurrence}


async def _standing(scenario, *keys, bounds=BOUNDS, now=NOW):
    result = await scenario.service().retrieve(READ, references(*keys), bounds, now=now)
    return standing(result, ROOM_KEY, ROOM_ENTITY, now)


async def test_standing_groups_show_the_room_position(scenario) -> None:
    await intention(scenario, "open")
    await intention(scenario, "other", origin_room=OTHER_ROOM)
    await intention(scenario, "settled", state="ended", ending="achieved", ended_at=AT)
    await scenario.claim("situation", part="situation")
    await scenario.entity(ROOM_ENTITY, pointer_kind="room", pointer_ref=ROOM)
    await write_edge(scenario, "situation", ROOM_ENTITY, "about")
    await scenario.item("new-message", at=AT + timedelta(minutes=1))
    view = await _standing(
        scenario, "other", "open", "settled", "situation", bounds=replace(BOUNDS, depth=0),
    )
    assert [entry.row.graph_id for entry in view.open] == ["open"]
    assert [entry.row.graph_id for entry in view.settled] == ["settled"]
    (entry,) = view.situation
    assert (entry.row.graph_id, entry.age, entry.items_since) == ("situation", NOW - AT, 1)
    assert (view.status, view.limits) == ("partial", ("depth",))


async def test_child_nests_under_its_parent(scenario) -> None:
    await intention(scenario, "parent")
    await intention(scenario, "child")
    await serves(scenario, "child", "parent")
    view = await _standing(scenario, "parent", "child")
    (parent,) = view.open
    (child,) = parent.children
    assert (parent.row.graph_id, child.row.graph_id) == ("parent", "child")


async def test_settled_intention_must_end_inside_the_result_bounds(scenario) -> None:
    await intention(scenario, "inside", state="ended", ending="achieved", ended_at=AT)
    await intention(scenario, "outside", state="ended", ending="achieved", ended_at=NOW)
    bounds = replace(BOUNDS, since=AT, until=AT + timedelta(hours=1))
    view = await _standing(scenario, "inside", "outside", bounds=bounds)
    assert [entry.row.graph_id for entry in view.settled] == ["inside"]


@pytest.mark.parametrize("field", ["deadline", "review_time", "trigger"])
async def test_past_deadline_review_or_unpublished_occurrence_is_due(scenario, field) -> None:
    at = NOW - timedelta(seconds=1)
    fields = {field: at} if field != "trigger" else {
        "trigger": _time_trigger(at), "delivery_mode": "choose_moment",
    }
    await intention(scenario, "due-fixture", **fields)
    view = await _standing(scenario, "due-fixture")
    (entry,) = view.open
    assert entry.due is True
    rendered = render_open_intention(
        entry, now=NOW, dm_rooms=frozenset(),
        reading_room=RoomFacts("signal", frozenset({ROOM}), UNIT, None),
    )
    assert " | due | " in rendered


async def test_future_deadline_review_and_trigger_are_not_due(scenario) -> None:
    future = NOW + timedelta(seconds=1)
    await intention(
        scenario, "future", deadline=future, review_time=future, trigger=_time_trigger(future),
        delivery_mode="choose_moment",
    )
    await intention(scenario, "due-control", deadline=NOW)
    view = await _standing(scenario, "future", "due-control")
    assert {entry.row.graph_id: entry.due for entry in view.open} == {
        "future": False, "due-control": True,
    }


async def test_same_recurring_trigger_stops_being_due_after_publication(scenario) -> None:
    rule = {"every": "hourly", "count": 2}
    await intention(scenario, "recurring", trigger=_time_trigger(AT, rule),
                    delivery_mode="choose_moment")
    read = scenario.service()
    anchors = references("recurring")
    before = await read.retrieve(READ, anchors, BOUNDS, now=NOW)
    # Both occurrences are due at the same pass clock, with and without publication.
    await SqlDueSchedules(scenario.engine).publish_due(tick_of_at(NOW))
    after = await read.retrieve(READ, anchors, BOUNDS, now=NOW)
    (unpublished,) = standing(before, ROOM_KEY, ROOM_ENTITY, NOW).open
    (finished,) = standing(after, ROOM_KEY, ROOM_ENTITY, NOW).open
    assert (unpublished.row.close_when["published_occurrences"], unpublished.due) == (0, True)
    assert (finished.row.close_when["published_occurrences"], finished.due) == (2, False)


async def test_unreadable_occurrence_count_is_partial_and_trigger_is_not_due(scenario) -> None:
    await intention(scenario, "time-intention", trigger=_time_trigger(AT),
                    delivery_mode="choose_moment")
    assert (await _standing(scenario, "time-intention")).open[0].due is True
    with refuse_count(scenario.engine, "SELECT graph_stimulus.graph_id, count("):
        view = await _standing(scenario, "time-intention")
    (entry,) = view.open
    assert (view.status, view.cause) == ("partial", "occurrences_unavailable")
    assert (entry.row.close_when["published_occurrences"], entry.due) == (None, False)


async def test_child_without_its_parent_is_top_level_and_keeps_the_reference(scenario) -> None:
    await intention(scenario, "parent")
    await intention(scenario, "child")
    await serves(scenario, "child", "parent")
    view = await _standing(scenario, "child", bounds=replace(BOUNDS, depth=0))
    (entry,) = view.open
    assert entry.row.graph_id == "child"
    assert [(edge.type, edge.target) for edge in entry.row.edges] == [("serves", "parent")]


async def test_settled_intention_carries_its_ending_and_witness(scenario) -> None:
    await intention(scenario, "settled", state="ended", ending="dropped", ended_at=AT)
    await scenario.item("witness")
    await write_edge(scenario, "settled", "witness", "closed_by")
    view = await _standing(scenario, "settled", bounds=replace(BOUNDS, depth=0))
    (entry,) = view.settled
    assert (entry.row.graph_id, entry.ending, entry.closed_by) == ("settled", "dropped", "witness")


async def test_a_lesson_about_the_room_does_not_appear_in_standing(scenario) -> None:
    await scenario.entity(ROOM_ENTITY, pointer_kind="room", pointer_ref=ROOM)
    for part in ("lesson", "situation"):
        await scenario.claim(part, part=part)
        await write_edge(scenario, part, ROOM_ENTITY, "about")
    view = await _standing(scenario, "lesson", "situation")
    assert [entry.row.graph_id for entry in view.situation] == ["situation"]
