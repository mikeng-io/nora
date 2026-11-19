"""A profile orders and names claims from a real entity-anchored retrieval."""

# ruff: noqa: F811 - pytest resolves the imported scenario fixture.

from dataclasses import replace
from datetime import timedelta

import pytest

from nora.memory.domain.presentations import EntityAnchor, profile
from nora.memory.domain.retrieval import ReadingRoom
from nora.memory.wire import claim_support
from tests.infra.test_retrieval_scope import (
    AT,
    BOUNDS,
    OTHER,
    PERSON,
    READ,
    UNIT,
    references,
    scenario,  # noqa: F401
    write_edge,
)


async def pointer(scenario, kind="person", ref=PERSON, *, graph_id="profile-anchor"):
    await scenario.entity(
        graph_id, pointer_kind=kind, pointer_ref=ref, name="Name from the stored pointer",
    )
    return EntityAnchor(graph_id, pointer_ref=ref)


async def about(scenario, key, *targets, part="fact", **fields):
    if part == "guess":
        fields |= {"prediction": "A generated prediction", "window_end": AT + timedelta(days=7)}
    await scenario.claim(key, part=part, persons=[PERSON], **fields)
    for target in targets:
        await write_edge(scenario, key, target, "about")


async def anchored(scenario, anchor, *, room=READ, bounds=BOUNDS):
    return await scenario.service().retrieve(room, references(anchor.graph_id), bounds)


def _ids(profile_read, part):
    return [row.graph_id for row in dict(profile_read.parts)[part]]


async def test_profile_has_only_its_three_parts_in_order(scenario) -> None:
    anchor = await pointer(scenario)
    for part in ("guess", "lesson", "fact", "situation", "trait"):
        await about(scenario, f"fixture-{part}", anchor.graph_id, part=part)
    result = profile(await anchored(scenario, anchor), anchor, {PERSON: "Fixture identity"})
    assert [(part, [row.graph_id for row in rows]) for part, rows in result.parts] == [
        ("fact", ["fixture-fact"]), ("trait", ["fixture-trait"]), ("guess", ["fixture-guess"]),
    ]


async def test_profile_precedence_keeps_outranked_claims_and_uses_recency(scenario) -> None:
    anchor = await pointer(scenario)
    specifications = (
        ("abduced", "abduced", 7), ("induced", "induced", 6), ("deduced", "deduced", 5),
        ("deduced-older", "deduced", 4), ("unseen-author", "told", 3), ("other-told", "told", 2),
        ("own-told", "told", 1),
    )
    for key, reasoning, age_in_hours in specifications:
        await about(scenario, key, anchor.graph_id, reasoning=reasoning,
                    asserted_at=AT + timedelta(hours=age_in_hours))
    await scenario.item("own-item", person=PERSON)
    await scenario.item("other-item", person=OTHER)
    await scenario.item("excluded-item", excluded_persons=[PERSON])
    await write_edge(scenario, "own-told", "own-item", "cites")
    await write_edge(scenario, "other-told", "other-item", "cites")
    await write_edge(scenario, "unseen-author", "excluded-item", "cites")
    result = await anchored(scenario, anchor)
    assert "excluded-item" not in {row.graph_id for row in result.rows}
    selected = profile(result, anchor, {})
    assert _ids(selected, "fact") == [
        "own-told", "unseen-author", "other-told", "deduced", "deduced-older", "induced", "abduced",
    ]


async def test_trait_order_uses_last_evidenced_time_and_keeps_unknowns_last(scenario) -> None:
    anchor = await pointer(scenario)
    for key, asserted_hours in (
        ("old-evidence", 5), ("recent-evidence", 1), ("unknown-new", 6), ("unknown-old", 3),
    ):
        await about(scenario, key, anchor.graph_id, part="trait",
                    asserted_at=AT + timedelta(hours=asserted_hours))
    support = claim_support(scenario.engine)
    for hours, key in ((4, "old-evidence"), (8, "recent-evidence")):
        at = AT + timedelta(hours=hours)
        await scenario.item(f"item-{key}", at=at)
        await write_edge(scenario, key, f"item-{key}", "cites")
        assert await support.rebuild(unit=UNIT, claim_id=key, at=at) == 1
    selected = profile(await anchored(scenario, anchor), anchor, {})
    assert _ids(selected, "trait") == [
        "recent-evidence", "old-evidence", "unknown-new", "unknown-old",
    ]


async def test_profile_header_uses_identity_name(scenario) -> None:
    anchor = await pointer(scenario)
    await about(scenario, "different-name", anchor.graph_id, content={"name": "Claim's name"})
    result = await anchored(scenario, anchor)
    selected = profile(result, anchor, {PERSON: "Identity view name"})
    assert selected.name == "Identity view name"
    assert _ids(selected, "fact") == ["different-name"]
    assert profile(result, anchor, {}).name is None


@pytest.mark.parametrize(
    ("kind", "ref"), [("room", "fixture-room"), ("unit", UNIT), ("nora", "nora")],
)
async def test_collective_or_nora_profile_uses_its_own_pointer(scenario, kind, ref) -> None:
    anchor = await pointer(scenario, kind, ref)
    person = await pointer(scenario, graph_id="person-anchor")
    await about(scenario, "pointer-claim", anchor.graph_id)
    await about(scenario, "member-claim", person.graph_id)
    selected = profile(await anchored(scenario, anchor), anchor, {ref: "Subject display name"})
    assert _ids(selected, "fact") == ["pointer-claim"]
    assert selected.name == "Subject display name"


async def test_profile_of_a_topic_uses_the_entity_name(scenario) -> None:
    anchor = EntityAnchor("topic")
    await scenario.entity(anchor.graph_id, name="Generated topic")
    await about(scenario, "topic-claim", anchor.graph_id)
    selected = profile(await anchored(scenario, anchor), anchor, {})
    assert (_ids(selected, "fact"), selected.name) == (["topic-claim"], "Generated topic")


async def test_profile_excludes_audience_hidden_claims(scenario) -> None:
    anchor = await pointer(scenario)
    await about(scenario, "visible-claim", anchor.graph_id)
    await about(scenario, "excluded-claim", anchor.graph_id, excluded_persons=[PERSON])
    assert _ids(profile(await anchored(scenario, anchor), anchor, {}), "fact") == ["visible-claim"]


async def test_profile_preserves_unavailable_and_partial_and_identifies_empty(scenario) -> None:
    anchor = await pointer(scenario)
    empty = await anchored(scenario, anchor)
    assert empty.status == "ok"
    assert profile(empty, anchor, {}).status == "nothing_found"

    await about(scenario, "untraversed-claim", anchor.graph_id)
    partial = await anchored(scenario, anchor, bounds=replace(BOUNDS, depth=0))
    assert partial.status == "partial"
    selection = profile(partial, anchor, {})
    assert (selection.status, selection.limits) == ("partial", ("depth",))

    missing = await anchored(scenario, anchor, room=ReadingRoom("signal", "absent-room"))
    assert missing.status == "unavailable" and missing.cause is not None
    selection = profile(missing, anchor, {})
    assert (selection.status, selection.cause) == ("unavailable", missing.cause)
