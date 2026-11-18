"""Exercise model admission through the registered graph in Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import cast
from uuid import NAMESPACE_DNS, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.agent.egress import LeakGuard
from nora.doctrine.loader import Doctrine
from nora.domain import graph_registration
from nora.domain.models import PersonRef
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY, REGISTRY
from nora.infra.sql.schema import MESSAGE_EDIT, MESSAGE_REACTION, MESSAGES
from nora.memory.adapters.admission_rows import SqlAdmissionRows
from nora.memory.adapters.admission_store import SqlAdmissionStore
from nora.memory.adapters.graph_store import SqlCognitiveGraph
from nora.memory.app.admission import AdmissionService, Stored, Unavailable
from nora.memory.app.admission_identity import AdmissionIdentity
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import (
    CreateClaim,
    CreateEntity,
    Link,
    MergeEntities,
    PassContext,
    Retire,
    Revise,
    Scope,
)
from nora.memory.domain.graph import StaleBaseError
from nora.memory.wire import admission
from nora.persons import Persons
from nora.policy import Conversation, Policy

PASS = PassContext(
    "u",
    "room-1",
    False,
    "wake",
    "admission",
    "pass-1",
    "trace-1",
    "model-1",
    "code-1",
    "instruction-1",
    "skill-1",
    "config-1",
    "identity-1",
    "policy-1",
)
DOCTRINE = "A synthetic instruction for the fixture that must stay inside the doctrine."
PERSON = PersonRef("fixture-person", "Fixture Person", "fixture-person")
ACCOUNT = str(uuid5(NAMESPACE_DNS, "admission.sender.fixture"))
SELF_ACCOUNT = str(uuid5(NAMESPACE_DNS, "admission.self.fixture"))
CLAIM_PROPOSAL = CreateClaim(
    id="claim-1",
    basis="inferred",
    reasoning="deduced",
    confidence="high",
    scope=Scope("u"),
    citations=("item-1",),
    content={"text": "The fixture records an event."},
)


def _authority() -> tuple[Persons, Policy, LeakGuard]:
    persons = Persons({PERSON.person_id: PERSON}, {("signal", ACCOUNT): PERSON.person_id})
    policy = Policy(
        (
            Conversation("signal", frozenset({"room-1"}), None, primary_id="room-1", unit="u"),
            Conversation(
                "signal",
                frozenset({ACCOUNT}),
                frozenset({PERSON.person_id}),
                primary_id=ACCOUNT,
                unit="u",
            ),
            Conversation(
                "signal", frozenset({"room-2"}), None, primary_id="room-2", unit="affiliate"
            ),
        )
    )
    guard = LeakGuard(Doctrine((), DOCTRINE, DOCTRINE, "fixture-sha", "", 0), "fixture-canary")
    return persons, policy, guard


def _service(engine: AsyncEngine, *, content_checks: bool = False) -> AdmissionService:
    persons, policy, guard = _authority()
    return admission(
        engine,
        persons,
        policy,
        guard,
        content_checks=content_checks,
        nora_accounts=(("signal", SELF_ACCOUNT),),
    )


def _fault_service(engine: AsyncEngine, writer: SqlCognitiveGraph) -> AdmissionService:
    persons, policy, guard = _authority()
    identity = AdmissionIdentity(persons, policy)
    store = SqlAdmissionStore(SqlAdmissionRows(engine, identity), writer)
    return AdmissionService(store, identity, guard, content_checks=False)


@pytest.fixture
async def engine(clean_pg_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_sql_engine(clean_pg_url)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(REGISTRY).values(id="item-1", unit="u", kind="item"))
        await conn.execute(
            sa.insert(MESSAGES).values(
                graph_id="item-1",
                unit="u",
                platform="signal",
                conversation_id="room-1",
                origin_room="room-1",
                conversation_type="group",
                sender_id=ACCOUNT,
                ts=1000,
                person_id=PERSON.person_id,
                persons=[PERSON.person_id],
            )
        )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _rows(engine: AsyncEngine, table: sa.Table) -> dict[str, sa.RowMapping]:
    async with engine.connect() as conn:
        return {row["id"]: row for row in (await conn.execute(sa.select(table))).mappings()}


async def test_batch_commits_valid_sibling_and_returns_typed_refusal(engine) -> None:
    bad = replace(CLAIM_PROPOSAL, id="bad", citations=("not-shown",))
    result = await _service(engine).admit((bad, CLAIM_PROPOSAL), PASS, ("item-1",))
    assert result.outcomes == (Refusal("citation_not_shown", "citations"), Stored("claim-1"))
    assert set(await _rows(engine, CLAIM)) == {"claim-1"}
    registry = await _rows(engine, REGISTRY)
    edges = await _rows(engine, EDGE)
    assert set(registry) == {"item-1", "claim-1", *edges}
    assert [(row["type"], row["src_id"], row["dst_id"]) for row in edges.values()] == [
        ("cites", "claim-1", "item-1"),
    ]


async def test_provenance_is_stamped_from_pass_on_claim_and_its_edge(engine) -> None:
    expected = {
        "writer": "admission",
        "occasion": "wake",
        "pass": "pass-1",
        "trace": "trace-1",
        "model": "model-1",
        "rev_code": "code-1",
        "rev_instruction": "instruction-1",
        "rev_skill": "skill-1",
        "rev_config": "config-1",
        "rev_identity": "identity-1",
        "rev_policy": "policy-1",
    }
    fake = dict.fromkeys(expected, "model-supplied")
    proposal = replace(CLAIM_PROPOSAL, provenance=fake)
    result = await _service(engine).admit((proposal,), PASS, ("item-1",))
    assert result.outcomes == (Stored("claim-1"),)
    claim = (await _rows(engine, CLAIM))["claim-1"]
    (edge,) = (await _rows(engine, EDGE)).values()
    for row in (claim, edge):
        assert {key: row[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"nested": ["sk" + "-" + "Q" * 25]}, "credential"),
        ({"nested": [DOCTRINE]}, "doctrine"),
    ],
)
async def test_always_on_protections_refuse_before_storage(engine, payload, reason) -> None:
    bad = replace(CLAIM_PROPOSAL, id="blocked", content=payload)
    result = await _service(engine).admit((bad, CLAIM_PROPOSAL), PASS, ("item-1",))
    assert result.outcomes == (Refusal(reason, "content"), Stored("claim-1"))
    assert set(await _rows(engine, CLAIM)) == {"claim-1"}
    assert all(row["src_id"] == "claim-1" for row in (await _rows(engine, EDGE)).values())
    assert "blocked" not in await _rows(engine, REGISTRY)


async def test_content_check_mode_screens_coordinates_and_identifiers(engine) -> None:
    coordinate = replace(CLAIM_PROPOSAL, content={"location": {"lat": 14.125, "lng": 18.254}})
    identifier = replace(coordinate, id="id-check", content={"text": ACCOUNT})
    on = await _service(engine, content_checks=True).admit(
        (coordinate, identifier), PASS, ("item-1",)
    )
    assert on.outcomes == (Refusal("content", "content"), Refusal("content", "content"))
    assert set(await _rows(engine, REGISTRY)) == {"item-1"}
    off = await _service(engine).admit((coordinate, identifier), PASS, ("item-1",))
    assert off.outcomes == (Stored("claim-1"), Stored("id-check"))


async def test_stale_revision_uses_the_stores_canonical_exception(engine) -> None:
    assert graph_registration.StaleBaseError is StaleBaseError
    svc = _service(engine)
    initial = await svc.admit((CLAIM_PROPOSAL,), PASS, ("item-1",))
    assert initial.outcomes == (Stored("claim-1"),)
    current = Revise(base="claim-1", replacement=replace(CLAIM_PROPOSAL, id="claim-2"))
    stale = Revise(base="claim-1", replacement=replace(CLAIM_PROPOSAL, id="stale"))
    still_current = Revise(base="claim-2", replacement=replace(CLAIM_PROPOSAL, id="claim-3"))
    result = await svc.admit((current, stale, still_current), PASS, ("claim-1", "item-1"))
    assert result.outcomes == (
        Stored("claim-2", "claim-1"),
        Refusal("stale_base", "base"),
        Stored("claim-3", "claim-2"),
    )

    copied = dict((await _rows(engine, CLAIM))["claim-3"])
    with pytest.raises(StaleBaseError):
        await SqlCognitiveGraph(engine).write_admission(
            CLAIM,
            dict(copied, id="direct-stale", replaces="claim-1"),
            (),
            base_state="superseded",
        )
    assert set(await _rows(engine, CLAIM)) == {"claim-1", "claim-2", "claim-3"}
    assert "direct-stale" not in await _rows(engine, REGISTRY)


async def test_returns_both_admitted_revision_and_retirement_ids(engine) -> None:
    revised = Revise(base="claim-1", replacement=replace(CLAIM_PROPOSAL, id="revision"))
    retired = Retire(
        id="retirement",
        base="revision",
        basis="inferred",
        scope=Scope("u"),
        reasoning="deduced",
        confidence="high",
    )
    result = await _service(engine).admit((CLAIM_PROPOSAL, revised, retired), PASS, ("item-1",))
    assert result.revisions == (Stored("revision", "claim-1"),)
    assert result.retirements == (Stored("retirement", "revision"),)
    rows = await _rows(engine, CLAIM)
    assert set(rows) == {"claim-1", "revision", "retirement"}
    assert rows["revision"]["state"] == "superseded"
    assert rows["retirement"]["state"] == "retired"
    assert rows["retirement"]["content"] == CLAIM_PROPOSAL.content


async def test_after_commit_failure_returns_unavailable_and_holds_dependants(engine) -> None:
    class MissingCommitReply:
        @asynccontextmanager
        async def begin(self):
            async with engine.begin() as conn:
                yield conn
            raise sa.exc.OperationalError(
                None, None, OSError("synthetic connection lost on commit")
            )

    writer = SqlCognitiveGraph(cast(AsyncEngine, MissingCommitReply()))
    revision = Revise(base="claim-1", replacement=replace(CLAIM_PROPOSAL, id="dependent"))
    result = await _fault_service(engine, writer).admit(
        (CLAIM_PROPOSAL, revision),
        PASS,
        ("item-1",),
    )
    assert result.outcomes == (Unavailable("claim-1", True), Unavailable("dependent", False))
    assert set(await _rows(engine, CLAIM)) == {"claim-1"}


async def test_definite_edge_failure_rolls_back_row_and_registry(engine) -> None:
    class BadEdge(SqlCognitiveGraph):
        async def write_admission(self, table, row, edges, **kwargs):
            broken = [dict(edge, type="rejected-by-postgres") for edge in edges]
            await super().write_admission(table, row, broken, **kwargs)

    svc = _fault_service(engine, BadEdge(engine))
    result = await svc.admit((CLAIM_PROPOSAL,), PASS, ("item-1",))
    assert result.outcomes == (Unavailable("claim-1", False),)
    assert set(await _rows(engine, REGISTRY)) == {"item-1"}
    assert not await _rows(engine, CLAIM)
    assert not await _rows(engine, EDGE)


async def test_merge_stores_relation_and_evidence_without_replacing_either_entity(engine) -> None:
    graph = SqlCognitiveGraph(engine)
    for id in ("source", "survivor"):
        await graph.create(
            ENTITY,
            {
                "id": id,
                "kind": "entity",
                "basis": "inferred",
                "unit": "u",
                "reasoning": "deduced",
                "confidence": "high",
                "state": "admitted",
                "exposure": "external",
                "writer": "fixture",
                "name": "a generated topic",
            },
        )
    merge = MergeEntities(
        id="merge",
        basis="inferred",
        scope=Scope("u"),
        source="source",
        survivor="survivor",
        citations=("item-1",),
        reasoning="deduced",
        confidence="high",
    )
    svc = _service(engine)
    shown = ("source", "survivor", "item-1")
    result = await svc.admit((merge,), PASS, shown)
    assert result.outcomes == (Stored("merge", "source"),)
    rows = await _rows(engine, ENTITY)
    assert (rows["source"]["state"], rows["survivor"]["state"]) == ("merged", "admitted")
    assert rows["source"]["replaces"] is None and rows["survivor"]["replaces"] is None
    assert {
        (row["type"], row["src_id"], row["dst_id"], row["basis"])
        for row in (await _rows(engine, EDGE)).values()
    } == {
        ("merged_into", "source", "survivor", "inferred"),
        ("cites", "source", "item-1", "inferred"),
    }

    revisions = tuple(
        Revise(
            base=base,
            replacement=CreateEntity(
                id=f"refresh-{base}",
                basis="inferred",
                scope=Scope("u"),
                reasoning="deduced",
                confidence="high",
                citations=("item-1",),
                name="a revised topic",
                links=(
                    Link(
                        type="interested_in",
                        basis="inferred",
                        source=f"refresh-{base}",
                        target=peer,
                        reasoning="deduced",
                        confidence="high",
                    ),
                ),
            ),
        )
        for base, peer in (("source", "survivor"), ("survivor", "source"))
    )
    result = await svc.admit(revisions, PASS, shown)
    assert result.revisions == (
        Stored("refresh-source", "source"),
        Stored("refresh-survivor", "survivor"),
    )


async def test_reads_registered_edit_and_reaction_authors_and_preserves_dm_origin(engine) -> None:
    async with engine.begin() as conn:
        source_id = await conn.scalar(
            sa.select(MESSAGES.c.id).where(MESSAGES.c.graph_id == "item-1")
        )
        await conn.execute(
            sa.update(MESSAGES)
            .where(MESSAGES.c.id == source_id)
            .values(
                conversation_type="dm",
                conversation_id=ACCOUNT,
                origin_room=ACCOUNT,
            )
        )
        for key in ("edit", "reaction"):
            await conn.execute(sa.insert(REGISTRY).values(id=key, unit="u", kind="item"))
        await conn.execute(
            sa.insert(MESSAGE_EDIT).values(
                graph_id="edit",
                unit="u",
                message_id=source_id,
                ts=1002,
                origin_room=ACCOUNT,
            )
        )
        await conn.execute(
            sa.insert(MESSAGE_REACTION).values(
                graph_id="reaction",
                unit="u",
                message_id=source_id,
                ts=1001,
                origin_room=ACCOUNT,
                sender_id=SELF_ACCOUNT,
                emoji="fixture",
                persons=[PERSON.person_id],
            )
        )
    basis = replace(CLAIM_PROPOSAL, reasoning="told", scope=Scope("u", persons=("unknown",)))
    edit = replace(basis, id="by-edit", citations=("edit",))
    reaction = replace(basis, id="by-reaction", citations=("reaction",))
    result = await _service(engine).admit((edit, reaction), PASS, ("edit", "reaction"))
    assert result.outcomes == (Stored("by-edit"), Refusal("told_without_person", "citations"))
    row = (await _rows(engine, CLAIM))["by-edit"]
    assert row["origin_room"] == f"signal:{ACCOUNT}" and row["persons"] == [None]
    other = replace(edit, id="affiliate", scope=Scope("affiliate"))
    result = await _service(engine).admit(
        (other,),
        replace(PASS, unit="affiliate", room="room-2"),
        ("edit",),
    )
    assert result.outcomes == (Refusal("citation_not_shown", "citations"),)
