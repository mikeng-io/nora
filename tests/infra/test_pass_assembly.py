"""Assemble a pass using real scoped anchor and retrieval readers."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from xml.etree import ElementTree

import pytest
import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.app.fire_engagement import AmbientEngagement
from nora.domain.blocks import ContextBlock
from nora.domain.events import Event
from nora.domain.lineage import ROW, Lineage, message
from nora.engagement.graph_context import EngagementGraph
from nora.engagement.self_presence import PresenceReader
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.schema import CULTURE_REVISION
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
from tests.infra.test_retrieval_scope import AT, DM, OTHER, PERSON, ROOM, Scenario, write_edge
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


class DelayedRetrieval(RetrievalService):
    def __init__(self, retrieval: SqlRetrieval, delay: float) -> None:
        super().__init__(retrieval, FakeQueryEmbedder())
        self._delay = delay

    async def retrieve(self, *args, **kwargs):
        result = await super().retrieve(*args, **kwargs)
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        completion = loop.time() + self._delay
        # Advance the real timeout scheduler while the read is still in flight.
        with patch.object(loop, "time", return_value=completion):
            loop.call_at(completion, ready.set)
            await ready.wait()
        return result


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
        ("profile", "nora-pointer"), ("lessons", None),
        ("profile", "room-here"), ("profile", "unit-pointer"),
        ("members", None), ("transcript", None),
        ("profile", "person-other"), ("profile", "person-here"),
        ("standing", None), ("related", None),
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
        "profile", "lessons", "standing", "related",
    }
    assert {element.attrib["status"] for element in blocks} == {"unavailable"}
    assert {element.attrib["cause"] for element in blocks} == {"anchor_read_failed"}
    assert all(element.text is None for element in blocks)


async def test_debrief_holds_only_standing_after_the_floor(scenario) -> None:
    await intention(scenario, "open-for-debrief")
    context = await _assembler(scenario).assemble("debrief", DebriefInput(()), _floor(), SCOPE, NOW)
    elements = _elements(context)
    assert [element.tag for element in elements] == ["members", "transcript", "standing"]
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
        profile_reader=assembler if assembler is not None else _assembler(scenario),
        clock=lambda: NOW,
    )
    return next(t._tool_func for t in tools if t.tool_name == "room_list")


async def _trait(scenario: Scenario, key: str, subject: str, body: str, **fields) -> None:
    await scenario.claim(key, part="trait", exposure="external", content={"text": body}, **fields)
    await write_edge(scenario, key, subject, "about")
    await scenario.item(f"{key}-evidence")
    await write_edge(scenario, key, f"{key}-evidence", "cites")


@pytest.mark.parametrize(("delay", "expected"), [
    (6.0, {"status": "ok", "part": "trait", "summary": "Room observation"}),
    (13.0, {"status": "unavailable", "note": "This room's profile could not be checked."}),
], ids=["within-graph-budget", "over-graph-budget"])
async def test_room_list_bounds_profiles_to_the_graph_read_budget(
    scenario, delay, expected,
) -> None:
    await pointers(scenario)
    await _trait(scenario, "room-culture", "room-here", "Room observation")
    retrieval = CountedRetrieval(scenario)
    assembler = PassAssembler(reader(scenario), DelayedRetrieval(retrieval, delay))
    result = await _room_list(scenario, assembler=assembler)()
    assert result["status"] == "ok"
    assert len(retrieval.reads) == 1
    assert next(r["culture"] for r in result["rooms"] if r["here"]) == expected


async def test_room_list_reads_each_rooms_profile_in_the_turn_audience(scenario) -> None:
    await pointers(scenario)
    await _trait(scenario, "local-culture", "room-here", "Keeps its decisions brief.\nSecond line.")
    await _trait(scenario, "unit-culture", "unit-pointer", "This belongs to the unit.")
    await _trait(scenario, "excluded-culture", "room-other", "Must stay outside this audience.",
                 excluded_identities=[PERSON])
    await _trait(scenario, "unverified-culture", "room-other", "Must wait for the live roster.",
                 excluded_identities=[OTHER])

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


async def test_room_list_culture_skips_an_uncited_dm_claim_and_marks_a_cited_one(scenario):
    await pointers(scenario)
    dm_origin = f"signal:{DM}"
    dm_lineage = Lineage.of({ROW: (message(dm_origin),)}).to_json()
    await scenario.item("dm-evidence", room=DM, dm=True, person=OTHER)
    await scenario.claim(
        "dm-uncited", part="trait", exposure="external", origin_room=dm_origin,
        content={"text": "Only the direct chat saw this."}, lineage=dm_lineage,
    )
    await write_edge(scenario, "dm-uncited", "room-here", "about")
    await write_edge(scenario, "dm-uncited", "dm-evidence", "cites")
    await _trait(scenario, "room-claim", "room-here", "Keeps its decisions brief.",
                 asserted_at=AT - timedelta(days=1))

    def here(result):
        return next(r["culture"] for r in result["rooms"] if r["here"])

    assert here(await _room_list(scenario)()) == {
        "status": "ok", "part": "trait", "summary": "Keeps its decisions brief.",
    }

    await scenario.claim(
        "dm-cited", part="trait", exposure="external", origin_room=dm_origin,
        content={"text": "Told in a direct chat."}, lineage=dm_lineage,
    )
    await write_edge(scenario, "dm-cited", "room-here", "about")
    await scenario.item("shared-evidence")
    await write_edge(scenario, "dm-cited", "shared-evidence", "cites")
    assert here(await _room_list(scenario)()) == {
        "status": "ok", "part": "trait", "summary": "Told in a direct chat. (origin=DM)",
    }


async def test_room_list_culture_with_no_cited_claim_reads_as_nothing_found(scenario):
    await pointers(scenario)
    await scenario.claim("uncited", part="trait", exposure="external",
                         content={"text": "Nobody can see where this came from."})
    await write_edge(scenario, "uncited", "room-here", "about")

    result = await _room_list(scenario)()
    assert next(r["culture"] for r in result["rooms"] if r["here"]) == {
        "status": "nothing_found", "note": "No profile claims were found for this room.",
    }


async def test_room_list_reports_nothing_found_on_an_empty_graph(scenario) -> None:
    legacy = SqlCultureStore(scenario.engine)
    revision = await legacy.append(
        key=ROOM, body="A legacy account.", written_at=int(NOW.timestamp() * 1000),
        established_by="fixture", citations=("fixture-citation",),
    )
    async with scenario.engine.connect() as conn:
        body = await conn.scalar(sa.select(CULTURE_REVISION.c.body).where(
            CULTURE_REVISION.c.id == revision.id))
    assert body == "A legacy account."
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
async def test_fire_prefill_uses_the_room_unit_person_and_nora_profiles(
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
    await scenario.claim("fixture-lesson", part="lesson")
    await write_edge(scenario, "fixture-lesson", "nora-pointer", "about")
    await intention(scenario, "fire-unit-standing", origin_room=None)
    await scenario.claim("fire-room-situation", part="situation")
    await write_edge(scenario, "fire-room-situation", "room-here", "about")
    calls = []
    fire = _fire_path(
        doctrine_store=doctrine_store, persons=scenario.persons, metrics=Metrics(),
        runner=FakeAgentRunner(_send_turns("Done."), on_call=calls.append),
        policy=scenario.policy, clock=lambda: NOW,
        ambient=AmbientEngagement(EngagementGraph(
            IdentityAccess.for_files(scenario.persons, scenario.policy),
            assembler=_assembler(scenario),
        )),
    )
    outcome = await fire.speak(Event(
        "reminder", "signal", alias, "group", PERSON, "Fixture reminder",
        lambda _ref, _now: "A reminder is due.",
    ))
    assert outcome == "sent"
    (call,) = calls
    prefill = call["tag_message"]
    for pointer, body in (
        ("room-here", "Room observation"), ("unit-pointer", "Unit observation"),
        ("person-here", "Person observation"),
    ):
        opening = f'<profile of="{pointer}"'
        assert opening in prefill
        fragment = prefill.split(opening, 1)[1]
        assert body in fragment.split("</profile>", 1)[0]
    assert ('<profile of="nora-pointer"' in prefill) is show_nora
    assert ("Nora observation" in prefill) is show_nora
    lesson = prefill.index("<lessons")
    assert "</lessons>" in prefill[lesson:]
    assert "fixture-lesson" in prefill[lesson:].split("</lessons>", 1)[0]
    assert lesson < prefill.index('<profile of="room-here"') < prefill.index(
        '<profile of="unit-pointer"'
    )
    if show_nora:
        assert prefill.index('<profile of="nora-pointer"') < lesson
    assert "Unrelated person observation" not in prefill
    assert "<culture" not in prefill
    assert prefill.count("<standing ") == 1
    standing = prefill.split("<standing ", 1)[1].split("</standing>", 1)[0]
    assert "fire-unit-standing" in standing
    assert "fire-room-situation" in standing
    assert all(f"<{tag}" not in prefill for tag in ("memos", "notes"))


async def test_fire_prefill_keeps_profiles_after_the_standing_deadline(
    scenario, doctrine_store,
) -> None:
    await pointers(scenario)
    await _trait(scenario, "room-culture", "room-here", "Room observation")
    retrieval = DelayedRetrieval(CountedRetrieval(scenario), 6.0)
    calls = []
    fire = _fire_path(
        doctrine_store=doctrine_store, persons=scenario.persons, metrics=Metrics(),
        runner=FakeAgentRunner(_send_turns("Done."), on_call=calls.append),
        policy=scenario.policy,
        ambient=AmbientEngagement(EngagementGraph(
            IdentityAccess.for_files(scenario.persons, scenario.policy),
            assembler=PassAssembler(reader(scenario), retrieval),
        )),
        clock=lambda: NOW,
    )
    outcome = await fire.speak(Event(
        "reminder", "signal", ROOM, "group", PERSON, "Fixture reminder",
        lambda _ref, _now: "A reminder is due.",
    ))
    assert outcome == "sent"
    (call,) = calls
    assert "Room observation" in call["tag_message"]


async def test_wake_profile_requires_every_agenda_room_to_permit_the_claim(scenario):
    from nora.domain.lineage import LiveRoom
    from nora.memory import ReadingRoom
    from tests.infra.test_retrieval_scope import OTHER_ROOM, UNIT

    await pointers(scenario)
    await scenario.claim("public-profile", exposure="external")
    await scenario.claim("excluded-in-second-room", exposure="external",
                         excluded_identities=[OTHER])
    for claim in ("public-profile", "excluded-in-second-room"):
        await write_edge(scenario, claim, "nora-pointer", "about")

    subjects = {("nora", "nora"): "Nora"}
    first = ReadingRoom("signal", ROOM, LiveRoom(frozenset({PERSON})), expected_unit=UNIT)
    second = ReadingRoom("signal", OTHER_ROOM, LiveRoom(frozenset({OTHER})), expected_unit=UNIT)
    reader = _assembler(scenario)
    control = await reader.read_profiles(first, subjects, NOW)
    assert "excluded-in-second-room" in control["nora", "nora"].block().body.text

    scoped = await reader.read_profiles(first, subjects, NOW, audience_rooms=(second,))
    assert "public-profile" in scoped["nora", "nora"].block().body.text
    assert "excluded-in-second-room" not in scoped["nora", "nora"].block().body.text

    wrong = replace(second, expected_unit="nobody's unit")
    refused = await reader.read_profiles(first, subjects, NOW, audience_rooms=(wrong,))
    assert refused["nora", "nora"].profile.status == "unavailable"
    assert refused["nora", "nora"].block().body.text == ""


async def test_wake_profile_needs_live_roster_evidence_for_audience_exclusions(scenario):
    from nora.domain.lineage import LiveRoom
    from nora.memory import ReadingRoom
    from tests.infra.test_retrieval_scope import OTHER_ROOM, UNIT

    await pointers(scenario)
    await scenario.claim("public-profile", exposure="external")
    await scenario.claim("exclusion-sensitive-profile", exposure="external",
                         excluded_identities=["fixture-unlisted-person"])
    for claim in ("public-profile", "exclusion-sensitive-profile"):
        await write_edge(scenario, claim, "nora-pointer", "about")

    subjects = {("nora", "nora"): "Nora"}
    first = ReadingRoom("signal", ROOM, LiveRoom(frozenset({PERSON})), expected_unit=UNIT)
    second = ReadingRoom("signal", OTHER_ROOM, LiveRoom(frozenset({OTHER})), expected_unit=UNIT)
    assembler = _assembler(scenario)
    allowed = await assembler.read_profiles(first, subjects, NOW, audience_rooms=(second,))
    assert "exclusion-sensitive-profile" in allowed["nora", "nora"].block().body.text

    unverified = replace(second, live=LiveRoom(frozenset(), available=False))
    restricted = await assembler.read_profiles(first, subjects, NOW, audience_rooms=(unverified,))
    profile = restricted["nora", "nora"].block().body.text
    assert "public-profile" in profile
    assert "exclusion-sensitive-profile" not in profile
