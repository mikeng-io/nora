"""Likes select only the audience's scoped, person-and-topic traits."""

# ruff: noqa: F811 - pytest resolves the imported scenario fixture.

from datetime import timedelta

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.domain.lineage import LiveRoom, RoomFacts
from nora.memory import wire
from nora.memory.adapters.graph_row_render import render_graph_row
from nora.memory.domain.presentations import likes, profile
from nora.memory.domain.retrieval import ReadingRoom, RetrievedSupport
from nora.memory.wire import claim_support
from tests.infra.test_profile_read import about, anchored, pointer
from tests.infra.test_retrieval_scope import (
    AT,
    OTHER,
    PERSON,
    READ,
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
                excluded_persons=[PERSON])
    entries = likes(await anchored(scenario, person), person)
    assert [entry.claim.graph_id for entry in entries] == ["visible-trait"]


def _reads(scenario):
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    lineage = LineagePolicy.from_policy(scenario.policy, access=access)
    return wire.graph_read_tools(scenario.engine, access, lineage, None, None)


async def test_likes_of_reads_a_resolved_person_for_the_rooms_audience(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic", name="Generated venue")
    await about(scenario, "trait", person.graph_id, "topic", part="trait")
    (read,) = await _reads(scenario).likes_of(READ, [PERSON], AT + timedelta(hours=1))
    assert (read.person, read.status) == (PERSON, "ok")
    assert [like.topic_name for like in read.likes] == ["Generated venue"]


async def test_likes_of_an_unresolved_person_is_unavailable_not_empty(scenario) -> None:
    await scenario.entity("topic", name="Generated venue")
    await pointer(scenario, ref=OTHER, graph_id="other-person")
    (read,) = await _reads(scenario).likes_of(READ, [PERSON], AT + timedelta(hours=1))
    assert (read.status, read.cause, read.likes) == ("unavailable", "person_unresolved", ())


async def test_likes_of_withholds_a_like_excluded_for_someone_in_the_live_roster(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic", name="Generated venue")
    await about(scenario, "trait", person.graph_id, "topic", part="trait",
                excluded_persons=[OTHER])
    reads, now = _reads(scenario), AT + timedelta(hours=1)
    alone = ReadingRoom("signal", ROOM, LiveRoom(frozenset({PERSON})))
    together = ReadingRoom("signal", ROOM, LiveRoom(frozenset({PERSON, OTHER})))
    (visible,) = await reads.likes_of(alone, [PERSON], now)
    (withheld,) = await reads.likes_of(together, [PERSON], now)
    assert [like.topic_name for like in visible.likes] == ["Generated venue"]
    assert withheld.likes == ()


async def test_likes_of_in_another_unit_reads_unavailable(scenario) -> None:
    person = await pointer(scenario)
    await scenario.entity("topic", name="Generated venue")
    await about(scenario, "trait", person.graph_id, "topic", part="trait")
    now = AT + timedelta(hours=1)
    (read,) = await _reads(scenario).likes_of(READ, [PERSON], now, unit="another-unit")
    assert (read.status, read.cause) == ("unavailable", "reading_room_unavailable")
