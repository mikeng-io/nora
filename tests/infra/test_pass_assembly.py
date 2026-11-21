"""Assemble a pass using real scoped anchor and retrieval readers."""

from dataclasses import replace
from xml.etree import ElementTree

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.domain.blocks import ContextBlock
from nora.memory import AnchorItem, CalledInput, DebriefInput, ReviewInput, wire
from nora.memory.adapters.pass_assembly import PassAssembler
from nora.memory.adapters.retrieval_store import SqlRetrieval
from nora.memory.app.retrieval import RetrievalService
from nora.testing.context_blocks import compose_fixture
from nora.testing.memory_index import FakeQueryEmbedder
from tests.infra.test_pass_anchors import NOW, SCOPE, pointers, reader
from tests.infra.test_retrieval_scope import OTHER, PERSON, Scenario, write_edge
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.infra.test_situation_staleness import intention, refuse_count

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


async def test_review_retrieves_due_rows_and_renders_only_the_floor(scenario) -> None:
    await intention(scenario, "due-for-review", deadline=NOW)
    floor = _floor()
    assembler, adapter = _counted(scenario)
    context = await assembler.assemble("review", ReviewInput(), floor, SCOPE, NOW)
    assert context.status == "ok"
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
