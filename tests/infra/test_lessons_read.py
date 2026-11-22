"""Read lesson claims and their citations through scope and exact-reference retrieval."""

from nora.domain.lineage import RoomFacts
from nora.memory.adapters.graph_row_render import render_graph_rows
from nora.memory.domain.presentations import lessons
from nora.memory.domain.retrieval import RetrievalAnchors
from tests.infra.test_retrieval_scope import AT, BOUNDS, READ, ROOM, UNIT, references, write_edge
from tests.infra.test_retrieval_scope import scenario as scope_scenario

scenario = scope_scenario


async def test_only_lesson_claims_appear(scenario) -> None:
    await scenario.claim("lesson", part="lesson")
    await scenario.claim("fact")
    await scenario.item("item")
    result = await scenario.service().retrieve(READ, references("lesson", "fact", "item"), BOUNDS)
    assert [row.graph_id for row in lessons(result)] == ["lesson"]


async def test_a_lesson_from_another_unit_is_absent(scenario) -> None:
    await scenario.claim("our-lesson", part="lesson")
    await scenario.claim("affiliate-lesson", unit="affiliate", part="lesson")
    result = await scenario.service().retrieve(
        READ, references("our-lesson", "affiliate-lesson"), BOUNDS,
    )
    assert [row.graph_id for row in lessons(result)] == ["our-lesson"]


async def test_each_lesson_renders_its_handle_and_citations(scenario) -> None:
    await scenario.claim("lesson-handle", part="lesson", reasoning="induced", confidence="medium")
    await scenario.item("correction", "Generated correction")
    await write_edge(scenario, "lesson-handle", "correction", "cites")
    result = await scenario.service().retrieve(READ, references("lesson-handle"), BOUNDS)
    rows = lessons(result)
    assert [(row.graph_id, [cite.graph_id for cite in row.evidence]) for row in rows] == [
        ("lesson-handle", ["correction"]),
    ]
    line = render_graph_rows(
        rows, now=AT, reading_room=RoomFacts("signal", frozenset({ROOM}), UNIT, None),
        dm_rooms=frozenset(),
    )
    assert line.startswith("claim: lesson-handle | basis=inferred (induced, medium confidence)")
    assert "citations=correction" in line
    assert "support=unknown" in line


async def test_a_retired_lesson_requires_an_exact_reference(scenario) -> None:
    phrase = "Generated practice at sunset"
    await scenario.claim("retired-lesson", part="lesson", state="retired", content={"text": phrase})
    await scenario.claim("active-lesson", part="lesson", content={"text": phrase})
    service = scenario.service()
    search = await service.retrieve(READ, RetrievalAnchors(query=phrase), BOUNDS)
    exact = await service.retrieve(
        READ, RetrievalAnchors(handles=("graph:retired-lesson",)), BOUNDS,
    )
    assert [row.graph_id for row in lessons(search)] == ["active-lesson"]
    assert [row.graph_id for row in lessons(exact)] == ["retired-lesson"]
