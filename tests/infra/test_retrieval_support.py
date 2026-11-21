"""Read the stored support tally through the graph's ordinary scoped read."""

# ruff: noqa: F811 - pytest resolves the imported scenario fixture.

from datetime import timedelta

from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.memory.domain.retrieval import RetrievedSupport
from nora.memory.wire import claim_support, memory_intake
from tests.infra.test_retrieval_scope import (
    AT,
    BOUNDS,
    PERSON,
    READ,
    UNIT,
    references,
    scenario,  # noqa: F401
    write_edge,
)


async def _read(scenario, *keys):
    result = await scenario.service().retrieve(READ, references(*keys), BOUNDS)
    assert result.status == "ok"
    return {row.graph_id: row for row in result.rows}


async def test_retrieves_the_tally_and_last_evidenced_time(scenario) -> None:
    later = AT + timedelta(days=1)
    await scenario.item("first", body="Generated first occasion")
    await scenario.item("second", body="Generated second occasion", at=later)
    await scenario.claim("trait", part="trait")
    await write_edge(scenario, "trait", "first", "cites")
    await write_edge(scenario, "trait", "second", "cites")
    assert await claim_support(scenario.engine).rebuild(unit=UNIT, claim_id="trait", at=later) == 2
    trait = (await _read(scenario, "trait"))["trait"]
    assert trait.part == "trait"
    assert trait.support == RetrievedSupport(2, later)


async def test_missing_tally_is_unknown(scenario) -> None:
    await scenario.claim("fact")
    fact = (await _read(scenario, "fact"))["fact"]
    assert fact.part == "fact"
    assert fact.support is None


async def test_rebuilt_tally_of_only_hidden_item_is_unknown(scenario) -> None:
    await scenario.claim("trait", part="trait")
    await scenario.item("evidence")
    await write_edge(scenario, "trait", "evidence", "cites")
    support = claim_support(scenario.engine)
    assert await support.rebuild(unit=UNIT, claim_id="trait", at=AT) == 1
    assert (await _read(scenario, "trait"))["trait"].support == RetrievedSupport(1, AT)

    registration = SqlItemRegistration(
        RegistrationScope(scenario.policy, scenario.persons),
        RuntimeRevisions({}, scenario.policy, scenario.persons),
    )
    await memory_intake(scenario.engine, registration).forget(
        unit=UNIT, author=PERSON, targets=("evidence",), at=AT,
    )
    assert await support.rebuild(unit=UNIT, claim_id="trait", at=AT) == 0
    rows = await _read(scenario, "trait")
    assert "evidence" not in rows
    assert rows["trait"].support is None


async def test_item_has_no_claim_part_or_support(scenario) -> None:
    await scenario.item("item")
    item = (await _read(scenario, "item"))["item"]
    assert item.kind == "item"
    assert (item.part, item.support) == (None, None)


async def test_only_pointer_entity_has_pointer_kind(scenario) -> None:
    await scenario.entity("person", pointer_kind="person", pointer_ref=PERSON)
    await scenario.entity("topic")
    rows = await _read(scenario, "person", "topic")
    assert rows["person"].pointer_kind == "person"
    assert rows["topic"].pointer_kind is None
