"""Resolve pass anchors against a generated unit through the SQL scope."""

from datetime import timedelta

import pytest

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.domain.graph_stimuli import GraphStimulus
from nora.memory.adapters.pass_anchors import SqlPassAnchors
from nora.memory.domain.pass_declarations import declaration
from nora.memory.domain.pass_inputs import (
    AmbientInput,
    AnchorItem,
    CalledInput,
    DebriefInput,
    PassScope,
    ProactiveInput,
    ReviewInput,
    WakeInput,
)
from nora.memory.domain.retrieval import RetrievalAnchors
from tests.infra.test_retrieval_scope import (
    AT,
    OTHER,
    OTHER_ROOM,
    PERSON,
    READ,
    ROOM,
    UNIT,
    Scenario,
    write_edge,
)
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.infra.test_situation_staleness import intention, refuse_count, serves

scenario = scope_scenario
SCOPE = PassScope(READ, UNIT)
NOW = AT + timedelta(hours=1)


def reader(scenario: Scenario) -> SqlPassAnchors:
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    return SqlPassAnchors(scenario.engine, access, LineagePolicy.from_policy(
        scenario.policy, access=access,
    ))


async def pointers(scenario: Scenario) -> None:
    for kind, ref, key in (
        ("person", PERSON, "person-here"), ("person", OTHER, "person-other"),
        ("room", f"signal:{ROOM}", "room-here"), ("room", f"signal:{OTHER_ROOM}", "room-other"),
        ("unit", UNIT, "unit-pointer"), ("nora", "nora", "nora-pointer"),
    ):
        await scenario.entity(key, pointer_kind=kind, pointer_ref=ref)


async def test_called_anchors_its_item_quote_room_and_resolved_people(scenario) -> None:
    await pointers(scenario)
    await scenario.entity("stray-person", pointer_kind="person", pointer_ref="unresolved")
    data = CalledInput(AnchorItem("addressing", "Own question", (PERSON, "unresolved")), "quote")
    result = await reader(scenario).build(declaration("called"), data, SCOPE, NOW)
    assert result.cause is None
    assert result.anchors == RetrievalAnchors(
        ("addressing", "quote", "room-here", "person-here"), query="Own question",
    )
    assert [subject.pointer_ref for subject in result.people] == [PERSON]


async def test_ambient_anchors_window_ids_text_room_and_people(scenario) -> None:
    await pointers(scenario)
    data = AmbientInput((
        AnchorItem("first", "Window one", (PERSON,)),
        AnchorItem("second", "Window two", (OTHER, PERSON)),
    ))
    result = await reader(scenario).build(declaration("ambient"), data, SCOPE, NOW)
    assert result.anchors == RetrievalAnchors(
        ("first", "second", "room-here", "person-other", "person-here"),
        query="Window one\nWindow two",
    )


async def test_proactive_anchors_intention_basis_parent_room_and_people(scenario) -> None:
    await pointers(scenario)
    await intention(scenario, "due", persons=[OTHER], origin_room=OTHER_ROOM)
    await intention(scenario, "parent")
    await scenario.claim("basis")
    await write_edge(scenario, "due", "basis", "rests_on")
    await serves(scenario, "due", "parent")
    result = await reader(scenario).build(
        declaration("proactive"), ProactiveInput("due"), SCOPE, NOW,
    )
    assert result.anchors == RetrievalAnchors(
        ("due", "basis", "parent", "room-other", "person-other"),
    )


async def test_wake_anchors_claimed_stimuli_items_rooms_people_and_intentions(scenario) -> None:
    await pointers(scenario)
    await scenario.item("stimulus-item")
    await intention(scenario, "dependent")
    await intention(scenario, "touched")
    await write_edge(scenario, "touched", "stimulus-item", "cites")
    await scenario.claim("non-intention")
    data = WakeInput((
        GraphStimulus(UNIT, "new_item", "stimulus-item", AT, "fixture", ROOM, PERSON,
                      dependents=("dependent", "non-intention")),
        GraphStimulus(UNIT, "intention_due", "dependent", AT, "fixture", OTHER_ROOM, OTHER),
    ))
    result = await reader(scenario).build(declaration("wake"), data, SCOPE, NOW)
    assert result.anchors == RetrievalAnchors((
        "stimulus-item", "dependent", "touched", "room-here", "room-other",
        "person-other", "person-here",
    ))


async def test_debrief_anchors_own_items_room_and_open_local_or_unit_intentions(scenario) -> None:
    await pointers(scenario)
    await intention(scenario, "local")
    await intention(scenario, "unit-wide", origin_room=None)
    await intention(scenario, "other-room", origin_room=OTHER_ROOM)
    await intention(scenario, "closed", state="ended", ending="achieved", ended_at=AT)
    data = DebriefInput(("act", "emitted"))
    result = await reader(scenario).build(declaration("debrief"), data, SCOPE, NOW)
    assert result.anchors == RetrievalAnchors(("act", "emitted", "local", "unit-wide", "room-here"))


@pytest.mark.parametrize("lessons", [(), ("named-lesson",)])
async def test_review_anchors_due_intentions_past_guesses_and_explicit_lessons(
    scenario, lessons,
) -> None:
    await intention(scenario, "past-deadline", deadline=NOW)
    await intention(scenario, "past-review", review_time=AT)
    await intention(
        scenario, "past-drop", drop_when_form="deadline", drop_when={"at": AT.isoformat()},
    )
    later = NOW + timedelta(hours=1)
    await intention(scenario, "future", deadline=later, review_time=later,
                    drop_when_form="deadline", drop_when=later.isoformat())
    await scenario.claim("past-guess", part="guess", prediction="Generated", window_end=AT)
    await scenario.claim("future-guess", part="guess", prediction="Generated", window_end=later)
    await scenario.claim("named-lesson", part="lesson")
    await scenario.claim("unrequested-lesson", part="lesson")
    result = await reader(scenario).build(declaration("review"), ReviewInput(lessons), SCOPE, NOW)
    assert result.anchors == RetrievalAnchors((
        "past-deadline", "past-drop", "past-guess", "past-review", *lessons,
    ))


async def test_a_failed_anchor_statement_returns_an_unavailable_cause(scenario) -> None:
    await pointers(scenario)
    data = CalledInput(AnchorItem("addressing", "Own question", (PERSON,)))
    anchors = reader(scenario)
    assert (await anchors.build(declaration("called"), data, SCOPE, NOW)).anchors.graph_ids
    with refuse_count(scenario.engine, "AS pointer_kind"):
        result = await anchors.build(declaration("called"), data, SCOPE, NOW)
    assert result.cause == "anchor_read_failed"
    assert result.anchors == RetrievalAnchors()
