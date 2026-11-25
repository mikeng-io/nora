"""Likes select only the audience's scoped, person-and-topic traits."""

# ruff: noqa: F811 - pytest resolves the imported scenario fixture.

from datetime import timedelta

from nora.domain.lineage import RoomFacts
from nora.memory.adapters.graph_row_render import render_graph_row
from nora.memory.domain.presentations import likes, profile
from nora.memory.domain.retrieval import RetrievedSupport
from nora.memory.wire import claim_support
from tests.infra.test_profile_read import about, anchored, pointer
from tests.infra.test_retrieval_scope import (
    AT,
    OTHER,
    PERSON,
    ROOM,
    UNIT,
    scenario,  # noqa: F401
    write_edge,
)


async def test_trait_about_person_and_topic_is_selected(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic", name="Generated activity")
    await scenario.item("evidence")
    content = {"text": "The fixture avoids the activity."}
    await about(scenario, "trait", person.graph_id, "topic", part="trait", content=content)
    await write_edge(scenario, "trait", "evidence", "cites")
    await claim_support(scenario.engine).rebuild(unit=UNIT, claim_id="trait", at=AT)
    result = await anchored(scenario, person)
    (like,) = likes(result, person)
    assert (like.topic.graph_id, like.claim.content) == ("topic", content)
    assert like.claim.support == RetrievedSupport(1, AT)
    (profile_trait,) = dict(profile(result, person, {}).parts)["trait"]
    room = RoomFacts("signal", frozenset({ROOM}), UNIT, frozenset({PERSON}))
    clock = {"now": AT + timedelta(hours=2), "reading_room": room, "dm_rooms": frozenset()}
    like_line = render_graph_row(like.claim, **clock)
    assert like_line == render_graph_row(profile_trait, **clock)
    assert "support=1, last evidenced=2h ago" in like_line


async def test_person_only_trait_is_absent_from_likes(scenario) -> None:
    person = await pointer(scenario)
    await about(scenario, "person-only", person.graph_id, part="trait")
    result = await anchored(scenario, person)
    assert any(row.graph_id == "person-only" for row in result.rows)
    assert likes(result, person) == ()


async def test_trait_about_two_people_is_absent_from_likes(scenario) -> None:
    person = await pointer(scenario)
    other = await pointer(scenario, ref=OTHER, graph_id="other-person")
    await about(scenario, "about-both", person.graph_id, other.graph_id, part="trait")
    result = await anchored(scenario, person)
    assert any(row.graph_id == other.graph_id for row in result.rows)
    assert likes(result, person) == ()


async def test_fact_about_person_and_topic_is_absent_from_likes(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic")
    await about(scenario, "topic-fact", person.graph_id, "topic")
    result = await anchored(scenario, person)
    assert any(row.graph_id == "topic-fact" for row in result.rows)
    assert likes(result, person) == ()


async def test_personal_response_requires_the_persons_own_told_evidence(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic")
    await scenario.item("own-message", person=PERSON)
    await scenario.item("other-message", person=OTHER)
    for claim_id, reasoning, item in (
        ("own-told", "told", "own-message"),
        ("other-told", "told", "other-message"),
        ("own-but-deduced", "deduced", "own-message"),
    ):
        await about(scenario, claim_id, person.graph_id, "topic", part="trait", reasoning=reasoning)
        await write_edge(scenario, claim_id, item, "cites")
    entries = likes(await anchored(scenario, person), person)
    evidence = {entry.claim.graph_id: (entry.item_sources, entry.personal_response)
                for entry in entries}
    assert evidence == {
        "own-told": (("messages",), True),
        "other-told": (("messages",), False),
        "own-but-deduced": (("messages",), False),
    }


async def test_zero_tally_on_like_renders_unknown(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic")
    await about(scenario, "trait", person.graph_id, "topic", part="trait")
    assert await claim_support(scenario.engine).rebuild(unit=UNIT, claim_id="trait", at=AT) == 0
    (like,) = likes(await anchored(scenario, person), person)
    assert like.claim.support is None
    room = RoomFacts("signal", frozenset({ROOM}), UNIT, frozenset({PERSON}))
    assert "support=unknown" in render_graph_row(
        like.claim, now=AT, reading_room=room, dm_rooms=frozenset(),
    )


async def test_like_excluded_for_an_audience_member_is_absent(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic")
    await about(scenario, "visible-trait", person.graph_id, "topic", part="trait")
    await about(scenario, "excluded-trait", person.graph_id, "topic", part="trait",
                excluded_identities=[PERSON])
    entries = likes(await anchored(scenario, person), person)
    assert [entry.claim.graph_id for entry in entries] == ["visible-trait"]
