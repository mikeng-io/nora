"""Assemble a pass using real scoped anchor and retrieval readers."""

from dataclasses import replace
from unittest.mock import patch
from xml.etree import ElementTree

import pytest
import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.blocks import ContextBlock
from nora.domain.events import Event
from nora.engagement.self_presence import PresenceReader
from nora.infra.sql.culture import SqlCultureStore
from nora.memory import AnchorItem, CalledInput, DebriefInput, ReviewInput, wire
from nora.memory.adapters.pass_assembly import PassAssembler
from nora.memory.adapters.retrieval_store import SqlRetrieval
from nora.memory.app.retrieval import RetrievalService
from nora.obs.metrics import Metrics
from nora.testing import FakeAdapter, FakeAgentRunner, FakeRecordStore
from nora.testing.context_blocks import compose_fixture
from nora.testing.memory_index import FakeQueryEmbedder
from tests.fakes import SinkSender
from tests.infra.test_pass_anchors import NOW, SCOPE, pointers, reader
from tests.infra.test_retrieval_scope import OTHER, PERSON, ROOM, Scenario, write_edge
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.infra.test_situation_staleness import intention, refuse_count
from tests.unit.test_fire_path import _fire_path, _send_turns
from tests.unit.test_room_list_and_read import _UNREACHABLE_TRANSIT

scenario = scope_scenario


class CountedRetrieval(SqlRetrieval):
    def __init__(self, scenario: Scenario) -> None:
        access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
        lineage = LineagePolicy.from_policy(scenario.policy, access=access)
        super().__init__(scenario.engine, access, lineage, None)
        self.reads = []

    def open_retrieval(self, room, concerns, as_of, bounds, now):
        self.reads.append((room, concerns, as_of, bounds, now))
        return super().open_retrieval(room, concerns, as_of, bounds, now)


def _assembler(scenario: Scenario) -> PassAssembler:
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    return wire.pass_assembler(
        scenario.engine, access, LineagePolicy.from_policy(scenario.policy, access=access),
        FakeQueryEmbedder(), None,
    )


def _counted(scenario: Scenario) -> tuple[PassAssembler, CountedRetrieval]:
    retrieval = CountedRetrieval(scenario)
    service = RetrievalService(retrieval, FakeQueryEmbedder())
    return PassAssembler(reader(scenario), service), retrieval


def _floor() -> tuple[ContextBlock, ...]:
    return (
        ContextBlock("members", compose_fixture("Fixture members"), order=300),
        ContextBlock("transcript", compose_fixture("Fixture transcript"), order=-300),
    )


def _elements(assembled):
    return ElementTree.fromstring("<context>" + assembled.render() + "</context>")  # noqa: S314


async def _called(scenario):
    await pointers(scenario)
    await scenario.item("called-item", body="Could you read this fixture?")
    return CalledInput(AnchorItem("called-item", "Could you read this fixture?", (PERSON, OTHER)))


async def test_called_renders_steady_blocks_floor_people_standing_and_related_in_order(
    scenario,
) -> None:
    data = await _called(scenario)
    await intention(scenario, "local-intention")
    await write_edge(scenario, "local-intention", "called-item", "cites")
    floor = _floor()
    context = await _assembler(scenario).assemble("called", data, floor, SCOPE, NOW)
    elements = _elements(context)
    assert [(element.tag, element.get("of")) for element in elements] == [
        ("graph_profile", "nora-pointer"), ("graph_lessons", None),
        ("graph_profile", "room-here"), ("graph_profile", "unit-pointer"),
        ("members", None), ("transcript", None),
        ("graph_profile", "person-other"), ("graph_profile", "person-here"),
        ("graph_standing", None), ("related", None),
    ]
    assert "called-item" in elements[-1].text
    assert "local-intention" in elements[-2].text
    assert context.blocks[4:6] == floor


async def test_called_retrieves_once_and_excludes_presented_claims_from_related(scenario) -> None:
    data = await _called(scenario)
    await scenario.claim("presented-claim")
    await write_edge(scenario, "presented-claim", "person-here", "about")
    await scenario.claim("related-claim")
    await write_edge(scenario, "related-claim", "called-item", "cites")
    assembler, adapter = _counted(scenario)
    context = await assembler.assemble("called", data, _floor(), SCOPE, NOW)
    assert len(adapter.reads) == 1
    (read,) = adapter.reads
    assert read[0] == SCOPE.room
    assert read[1] == frozenset({PERSON, OTHER})
    elements = _elements(context)
    assert "presented-claim" in elements[7].text
    assert "related-claim" in elements[-1].text
    assert "presented-claim" not in elements[-1].text


async def test_failed_anchors_mark_every_presentation_unavailable_and_keep_floor(scenario) -> None:
    data = await _called(scenario)
    floor = _floor()
    assembler, adapter = _counted(scenario)
    assert (await assembler.assemble("called", data, floor, SCOPE, NOW)).status == "ok"
    with refuse_count(scenario.engine, "AS pointer_kind"):
        context = await assembler.assemble("called", data, floor, SCOPE, NOW)
    assert (context.status, context.cause) == (
        "unavailable", "anchor_read_failed",
    )
    assert len(adapter.reads) == 1
    assert tuple(block for block in context.blocks if block in floor) == floor
    blocks = [element for element in _elements(context) if element.tag not in {
        "members", "transcript",
    }]
    assert {element.tag for element in blocks} == {
        "graph_profile", "graph_lessons", "graph_standing", "related",
    }
    assert {element.attrib["status"] for element in blocks} == {"unavailable"}
    assert {element.attrib["cause"] for element in blocks} == {"anchor_read_failed"}
    assert all(element.text is None for element in blocks)


async def test_debrief_holds_only_standing_after_the_floor(scenario) -> None:
    await intention(scenario, "open-for-debrief")
    context = await _assembler(scenario).assemble("debrief", DebriefInput(()), _floor(), SCOPE, NOW)
    elements = _elements(context)
    assert [element.tag for element in elements] == ["members", "transcript", "graph_standing"]
    assert "open-for-debrief" in elements[-1].text


async def test_review_skips_intentions_and_renders_only_the_floor_when_nothing_is_due(scenario):
    await intention(scenario, "due-for-review", deadline=NOW)
    floor = _floor()
    assembler, adapter = _counted(scenario)
    context = await assembler.assemble("review", ReviewInput(), floor, SCOPE, NOW)
    assert context.status == "nothing_found"
    assert len(adapter.reads) == 1
    assert context.blocks == floor
    assert [element.tag for element in _elements(context)] == ["members", "transcript"]


async def test_as_of_passes_through_to_the_real_retrieval(scenario) -> None:
    data = await _called(scenario)
    cutoff = await scenario.now()
    await scenario.item("later-quote")
    data = replace(data, quotes="later-quote")
    assembler, adapter = _counted(scenario)
    context = await assembler.assemble(
        "called", data, _floor(), replace(SCOPE, as_of=cutoff), await scenario.now(),
    )
    (read,) = adapter.reads
    assert (read[2], read[3].until) == (cutoff, cutoff)
    body = _elements(context)[-1].text
    assert "called-item" in body
    assert "later-quote" not in body


def _room_list(scenario: Scenario, *, assembler: PassAssembler | None = None):
    persons, policy = scenario.persons, scenario.policy
    directory = PresenceReader(
        policy=policy, adapters={"signal": FakeAdapter(account="fixture")}, rosters={},
    )
    tools = build_tools(
        store=FakeRecordStore(), persons=persons, conversation_id=ROOM,
        conversation_type="group", deliverer=SinkSender(), metrics=Metrics(),
        room=Room(platform="signal", conversation_id=ROOM, roster_source=None, persons=persons),
        transit_source=_UNREACHABLE_TRANSIT, rooms=directory,
        graph_profiles=assembler if assembler is not None else _assembler(scenario),
        clock=lambda: NOW,
    )
    return next(t._tool_func for t in tools if t.tool_name == "room_list")


async def _trait(scenario: Scenario, key: str, subject: str, body: str, **fields) -> None:
    await scenario.claim(key, part="trait", exposure="external", content={"text": body}, **fields)
    await write_edge(scenario, key, subject, "about")


async def test_room_list_bounds_the_graph_profile_read_to_five_seconds(scenario) -> None:
    assembler = _assembler(scenario)
    room_list = _room_list(scenario, assembler=assembler)

    with patch.object(assembler, "read_profiles", wraps=assembler.read_profiles) as read:
        result = await room_list()

    assert result["status"] == "ok"
    read.assert_awaited_once()
    assert read.await_args.kwargs.get("timeout_seconds") == 5.0


async def test_room_list_reads_each_rooms_graph_profile_in_the_turn_audience(scenario) -> None:
    await pointers(scenario)
    await _trait(scenario, "local-culture", "room-here", "Keeps its decisions brief.\nSecond line.")
    await _trait(scenario, "unit-culture", "unit-pointer", "This belongs to the unit.")
    await _trait(scenario, "excluded-culture", "room-other", "Must stay outside this audience.",
                 excluded_persons=[PERSON])
    await _trait(scenario, "unverified-culture", "room-other", "Must wait for the live roster.",
                 excluded_persons=[OTHER])

    room_list = _room_list(scenario)
    restricted = await room_list()
    assert all(r["culture"]["status"] == "nothing_found" and "summary" not in r["culture"]
               for r in restricted["rooms"] if not r["here"])

    await _trait(scenario, "sibling-culture", "room-other", "Keeps its meetings informal.")
    result = await room_list()
    assert result["status"] == "ok"
    assert [r["culture"] for r in result["rooms"] if r["here"]] == [
        {"status": "ok", "part": "trait", "summary": "Keeps its decisions brief."},
    ]
    assert {"status": "ok", "part": "trait", "summary": "Keeps its meetings informal."} in [
        r["culture"] for r in result["rooms"] if not r["here"]
    ]
    assert "This belongs to the unit." not in str(result)
    assert "Must stay outside this audience." not in str(result)
    assert "Must wait for the live roster." not in str(result)


async def test_room_list_reports_nothing_found_on_an_empty_graph(scenario) -> None:
    legacy = SqlCultureStore(scenario.engine)
    await legacy.append(key=ROOM, body="A legacy account.", written_at=int(NOW.timestamp() * 1000),
                        established_by="fixture", citations=("fixture-citation",))
    assert (await legacy.head(key=ROOM)).body == "A legacy account."
    room_list = _room_list(scenario)
    result = await room_list()
    assert result["status"] == "ok" and result["rooms"]
    assert all(
        r["culture"]["status"] == "nothing_found" and "summary" not in r["culture"]
        for r in result["rooms"]
    )

    await pointers(scenario)
    await _trait(scenario, "local-culture", "room-here", "Keeps its decisions brief.")
    result = await room_list()
    assert [r["culture"] for r in result["rooms"] if r["here"]] == [
        {"status": "ok", "part": "trait", "summary": "Keeps its decisions brief."},
    ]


async def test_room_list_reports_missing_graph_schema_as_unavailable(scenario) -> None:
    room_list = _room_list(scenario)
    assert all(r["culture"]["status"] == "nothing_found" for r in (await room_list())["rooms"])

    async with scenario.engine.begin() as conn:
        await conn.execute(sa.text('ALTER TABLE "claim" RENAME TO "fixture_removed_claim"'))
    try:
        result = await room_list()
        assert result["status"] == "ok" and result["rooms"]
        assert all(
            r["culture"]["status"] == "unavailable" and "summary" not in r["culture"]
            for r in result["rooms"]
        )
    finally:
        async with scenario.engine.begin() as conn:
            await conn.execute(sa.text('ALTER TABLE "fixture_removed_claim" RENAME TO "claim"'))


@pytest.mark.parametrize("show_nora", (True, False))
async def test_fire_prefill_uses_the_room_unit_person_and_nora_graph_profiles(
    scenario, doctrine_store, show_nora,
) -> None:
    alias = "fixture-arriving-thread"
    here, sibling, dm = scenario.policy.conversations
    here = replace(here, ids=here.ids | {alias})
    sibling = replace(sibling, location=not show_nora)
    rooms = (here, sibling, dm)
    policy = replace(scenario.policy, conversations=rooms, _by_id={
        (room.platform, key): room for room in rooms for key in room.ids
    })
    scenario = replace(scenario, policy=policy)
    await pointers(scenario)
    for pointer, body in (
        ("room-here", "Room observation"), ("unit-pointer", "Unit observation"),
        ("person-here", "Person observation"), ("nora-pointer", "Nora observation"),
        ("person-other", "Unrelated person observation"),
    ):
        await _trait(scenario, f"trait-{pointer}", pointer, body)
    calls = []
    fire = _fire_path(
        doctrine_store=doctrine_store, persons=scenario.persons, metrics=Metrics(),
        runner=FakeAgentRunner(_send_turns("Done."), on_call=calls.append),
        policy=scenario.policy, graph_profiles=_assembler(scenario), clock=lambda: NOW,
    )
    outcome = await fire.speak(Event(
        "reminder", "signal", alias, "group", PERSON, "Fixture reminder",
        lambda _ref, _now: "A reminder is due.",
    ))
    assert outcome == "sent"
    (call,) = calls
    prefill = call["pre_fill"][1]["content"][0]["text"]
    for pointer, body in (
        ("room-here", "Room observation"), ("unit-pointer", "Unit observation"),
        ("person-here", "Person observation"),
    ):
        opening = f'<graph_profile of="{pointer}"'
        assert opening in prefill
        fragment = prefill.split(opening, 1)[1]
        assert body in fragment.split("</graph_profile>", 1)[0]
    assert ('<graph_profile of="nora-pointer"' in prefill) is show_nora
    assert ("Nora observation" in prefill) is show_nora
    assert "Unrelated person observation" not in prefill
    assert "<culture" not in prefill
