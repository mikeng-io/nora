"""Graph presentation blocks keep the registry, retrieval status and subject."""

from dataclasses import replace
from xml.etree import ElementTree

import pytest

from nora.domain.block_types import BLOCK_TYPES
from nora.domain.blocks import BlockDeclaration, assemble, assemble_declared
from nora.memory.adapters.presentation_blocks import GraphPresentationBlocks
from nora.memory.domain.presentations import EntityAnchor
from nora.memory.domain.retrieval import RetrievalBounds, RetrievalResult, RetrievedEdge
from tests.unit.test_graph_row_render import CLAIM, DM_ROOMS, EARLIER, GROUP, NOW

BOUNDS = RetrievalBounds(2, 8, 40, EARLIER, NOW)
SUBJECT = EntityAnchor("fixture-entity", "fixture-person")
NAMES = {"fixture-person": "Fixture <name>"}
KINDS = ("graph_profile", "graph_standing", "graph_lessons", "related")


def test_graph_types_describe_their_owning_store() -> None:
    stores = {
        "graph_profile": "cognitive_graph.profile",
        "graph_standing": "cognitive_graph.standing",
        "graph_lessons": "cognitive_graph.lessons",
        "related": "cognitive_graph.related",
    }
    for name, store in stores.items():
        assert BLOCK_TYPES[name].description
        assert BLOCK_TYPES[name].store == store


@pytest.mark.parametrize("status", ["empty", "unavailable", "partial"])
def test_graph_blocks_render_retrieval_status(status) -> None:
    blocks = GraphPresentationBlocks(NOW, GROUP, DM_ROOMS)
    row = replace(CLAIM, edges=(RetrievedEdge("about", CLAIM.graph_id, SUBJECT.graph_id),))
    result = RetrievalResult(
        status if status != "empty" else "nothing_found", BOUNDS,
        () if status == "empty" else (row,),
        ("rows",) if status == "partial" else (),
        'a read failed: <fixture> "cause"' if status == "unavailable" else None,
    )
    instances = (
        blocks.profile_block(result, SUBJECT, NAMES)[0],
        blocks.standing_block(result, "signal:room", SUBJECT.graph_id)[0],
        blocks.lessons_block(result, result.rows),
        blocks.related_block(result, result.rows),
    )
    declaration = tuple(
        BlockDeclaration(name, i, "Read a graph presentation") for i, name in enumerate(KINDS)
    )
    rendered = assemble_declared(declaration, instances)
    assert assemble(instances, sep="\n\n") == rendered
    elements = ElementTree.fromstring(  # noqa: S314 - rendered test fixtures
        "<context>" + rendered + "</context>",
    )
    assert tuple(element.tag for element in elements) == KINDS
    assert {element.get("status") for element in elements} == {status}
    if status == "unavailable":
        assert {element.get("cause") for element in elements} == {result.cause}
    if status == "partial":
        assert {element.get("limit") for element in elements} == {"rows"}
        assert all(row.graph_id in (elements[i].text or "") for i in (0, 2, 3))
    else:
        assert all(element.text is None for element in elements)


def test_profile_blocks_identify_each_subject_by_id_and_identity_name() -> None:
    blocks = GraphPresentationBlocks(NOW, GROUP, DM_ROOMS)
    other = EntityAnchor("fixture-other-entity", "fixture-other")
    selected = [
        blocks.profile_block(RetrievalResult("nothing_found", BOUNDS), subject, {
            **NAMES, "fixture-other": "Another fixture name",
        })[0]
        for subject in (SUBJECT, other)
    ]
    rendered = assemble_declared((BlockDeclaration("graph_profile", 0, "Read claims"),), selected)
    elements = ElementTree.fromstring("<context>" + rendered + "</context>")  # noqa: S314
    assert [(row.get("of"), row.get("name")) for row in elements] == [
        ("fixture-entity", "Fixture <name>"), ("fixture-other-entity", "Another fixture name"),
    ]
