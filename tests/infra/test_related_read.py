"""Render the items and claims reached by the walk outside other presentations."""

from nora.domain.lineage import RoomFacts
from nora.memory.adapters.graph_row_render import render_related_rows
from nora.memory.domain.presentations import EntityAnchor, profile, related
from tests.infra.test_retrieval_scope import AT, BOUNDS, READ, ROOM, UNIT, references, write_edge
from tests.infra.test_retrieval_scope import scenario as scope_scenario
from tests.infra.test_situation_staleness import intention

scenario = scope_scenario


async def test_a_claim_already_shown_in_a_profile_is_absent_from_related(scenario) -> None:
    await scenario.entity("subject")
    await scenario.claim("profile-fact")
    await write_edge(scenario, "profile-fact", "subject", "about")
    await scenario.item("related-item")
    result = await scenario.service().retrieve(READ, references("subject", "related-item"), BOUNDS)
    shown = {row.graph_id for _, rows in profile(result, EntityAnchor("subject"), {}).parts
             for row in rows}
    assert shown == {"profile-fact"}
    assert [row.graph_id for row in related(result, shown)] == ["related-item"]


async def test_related_excludes_intentions_and_entities(scenario) -> None:
    await scenario.entity("subject")
    await intention(scenario, "intention")
    await scenario.claim("claim")
    result = await scenario.service().retrieve(
        READ, references("subject", "intention", "claim"), BOUNDS,
    )
    assert {row.kind for row in result.rows} == {"entity", "intention", "claim"}
    assert [row.graph_id for row in related(result, ())] == ["claim"]


async def test_related_renders_two_hop_item_with_distance_two(scenario) -> None:
    await scenario.entity("subject")
    await scenario.claim("claim")
    await scenario.item("two-hops")
    await write_edge(scenario, "claim", "subject", "about")
    await write_edge(scenario, "claim", "two-hops", "cites")
    result = await scenario.service().retrieve(READ, references("subject"), BOUNDS)
    rows = related(result, {"claim"})
    (row,) = rows
    assert (row.graph_id, row.distance) == ("two-hops", 2)
    line = render_related_rows(
        rows, now=AT, reading_room=RoomFacts("signal", frozenset({ROOM}), UNIT, None),
        dm_rooms=frozenset(),
    )
    assert line.startswith("item: two-hops | ")
    assert "distance=2" in line


async def test_related_keeps_a_row_marked_as_already_shown(scenario) -> None:
    await scenario.item("seen-before")
    result = await scenario.service().retrieve(
        READ, references("seen-before"), BOUNDS, already_shown=frozenset({"seen-before"}),
    )
    (row,) = related(result, ())
    assert (row.graph_id, row.already_shown) == ("seen-before", True)
