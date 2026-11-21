"""Read back derived and model-proposed about links from Postgres admission."""

from dataclasses import replace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.models import PersonRef
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY
from nora.memory.adapters.graph_store import SqlCognitiveGraph
from nora.memory.app.admission import AdmissionService, Stored
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import AddEdge, Link, Revise, Scope
from nora.memory.wire import admission
from tests.infra.test_cognitive_graph_admission import (
    CLAIM_PROPOSAL,
    PASS,
    PERSON,
    _authority,
    _rows,
)
from tests.infra.test_cognitive_graph_admission import engine as admitted_message_engine

engine = admitted_message_engine

OTHER = PersonRef("other-fixture-person", "Other Fixture Person", "other-fixture-person")


def _service(engine: AsyncEngine) -> AdmissionService:
    persons, policy, guard = _authority()
    persons = replace(persons, by_person={**persons.by_person, OTHER.person_id: OTHER})
    return admission(engine, persons, policy, guard, content_checks=False)


async def _pointer(engine: AsyncEngine, person: PersonRef) -> str:
    await SqlCognitiveGraph(engine).create(ENTITY, {
        "id": f"pointer:{person.person_id}", "unit": PASS.unit, "kind": "entity",
        "name": person.person_id, "basis": "computed", "state": "admitted",
        "exposure": "internal", "writer": "fixture",
        "pointer_kind": "person", "pointer_ref": person.person_id,
    })
    return f"pointer:{person.person_id}"


async def _topic(engine: AsyncEngine, *, unit: str = PASS.unit) -> str:
    ref = f"topic:{unit}"
    await SqlCognitiveGraph(engine).create(ENTITY, {
        "id": ref, "unit": unit, "kind": "entity", "name": "Fixture topic",
        "basis": "inferred", "reasoning": "deduced", "confidence": "high",
        "state": "admitted", "exposure": "external", "writer": "fixture",
    })
    return ref


async def _about(engine: AsyncEngine) -> set[tuple[str, str, str]]:
    found = [
        (row["src_id"], row["dst_id"], row["basis"])
        for row in (await _rows(engine, EDGE)).values() if row["type"] == "about"
    ]
    unique = set(found)
    assert len(found) == len(unique)
    return unique


async def test_admitted_claim_derives_one_edge_per_person(engine: AsyncEngine) -> None:
    first = await _pointer(engine, PERSON)
    second = await _pointer(engine, OTHER)
    proposal = replace(CLAIM_PROPOSAL, scope=Scope(
        PASS.unit, persons=(PERSON.person_id, OTHER.person_id, PERSON.person_id),
    ))
    admission_result = await _service(engine).admit((proposal,), PASS, proposal.citations)
    assert admission_result.outcomes == (Stored(proposal.id),)
    assert await _about(engine) == {
        (proposal.id, first, "computed"), (proposal.id, second, "computed"),
    }


async def test_claim_revision_rederives_about_from_its_own_persons(engine: AsyncEngine) -> None:
    first = await _pointer(engine, PERSON)
    second = await _pointer(engine, OTHER)
    initial = replace(CLAIM_PROPOSAL, scope=Scope(PASS.unit, persons=(PERSON.person_id,)))
    current = replace(initial, id="claim-2", scope=Scope(PASS.unit, persons=(OTHER.person_id,)))
    result = await _service(engine).admit(
        (initial, Revise(base=initial.id, replacement=current)), PASS, initial.citations,
    )
    assert result.outcomes == (Stored(initial.id), Stored(current.id, initial.id))
    assert await _about(engine) == {
        (initial.id, first, "computed"), (current.id, second, "computed"),
    }


async def test_about_skips_unresolved_people_and_never_creates_missing_pointers(
    engine: AsyncEngine,
) -> None:
    pointer = await _pointer(engine, PERSON)
    proposal = replace(CLAIM_PROPOSAL, scope=Scope(
        PASS.unit, persons=(PERSON.person_id, "unmapped-fixture-person", OTHER.person_id),
    ))
    result = await _service(engine).admit((proposal,), PASS, proposal.citations)
    assert result.outcomes == (Stored(proposal.id),)
    assert (await _rows(engine, CLAIM))[proposal.id]["persons"] == [
        PERSON.person_id, None, OTHER.person_id,
    ]
    assert set(await _rows(engine, ENTITY)) == {pointer}
    assert await _about(engine) == {(proposal.id, pointer, "computed")}


async def test_inferred_about_from_claim_and_recorded_message_is_admitted(
    engine: AsyncEngine,
) -> None:
    topic = await _topic(engine)
    await _pointer(engine, PERSON)
    message, = CLAIM_PROPOSAL.citations
    link = Link(
        type="about", source=CLAIM_PROPOSAL.id, target=topic, basis="inferred",
        reasoning="deduced", confidence="high",
    )
    claim = replace(CLAIM_PROPOSAL, links=(link,))
    edge = replace(_proposal(message, topic), scope=Scope(PASS.unit, persons=(PERSON.person_id,)))
    result = await _service(engine).admit((claim, edge), PASS, (message, topic))
    assert result.outcomes == (Stored(claim.id), Stored(edge.id))
    assert await _about(engine) == {(claim.id, topic, "inferred"), (message, topic, "inferred")}


def _proposal(source: str, target: str, *, basis: str = "inferred") -> AddEdge:
    return AddEdge(
        id="inferred-about", type="about", source=source, target=target, basis=basis,
        confidence="high" if basis == "inferred" else None,
        reasoning="deduced" if basis == "inferred" else None,
        scope=Scope(PASS.unit),
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("computed", Refusal("occasion_basis", "basis")),
        ("not-entity", Refusal("illegal_endpoints", "target")),
        ("other-unit", Refusal("illegal_endpoints", "target")),
    ),
)
async def test_invalid_about_is_refused(
    engine: AsyncEngine, case: str, expected: Refusal,
) -> None:
    message, = CLAIM_PROPOSAL.citations
    topic = await _topic(engine, unit="affiliate" if case == "other-unit" else PASS.unit)
    proposal = _proposal(
        message, message if case == "not-entity" else topic,
        basis="computed" if case == "computed" else "inferred",
    )
    svc = _service(engine)
    result = await svc.admit((proposal,), PASS, (message, topic))
    assert result.outcomes == (expected,)
    assert not await _about(engine)

    if case == "other-unit":
        topic = await _topic(engine)
    allowed = _proposal(message, topic)
    positive = await svc.admit((allowed,), PASS, (message, topic))
    assert positive.outcomes == (Stored(allowed.id),)
    assert await _about(engine) == {(message, topic, "inferred")}
