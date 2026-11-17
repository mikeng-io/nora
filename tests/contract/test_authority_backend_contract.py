"""Complete asserted-scope reads across file, fake and real Keto backends."""

import aiohttp
import pytest

from nora.domain.managed import (
    ROLE_OBSERVED_RELATION,
    CheckResult,
    OutcomeStatus,
    Verdict,
    role_object_id,
)
from nora.domain.managed_ports import RoleRef, ScopedRelation
from nora.domain.management import Scope, ScopeKind
from nora.infra.ory.keto import KetoClient
from nora.people.adapters.file_backend import FileAuthorityBackend
from nora.people.adapters.keto_authority import KetoAuthorityBackend
from nora.policy import load_policy
from nora.testing.managed_authority import FakeAuthorityBackend
from tests.integration.managed_ory import keto as keto
from tests.unit.test_managed_admission import POLICY

ROOM = Scope(ScopeKind.ROOM, "signal/generated-room-a")
PARENT = Scope(ScopeKind.UNIT, "generated-parent")
OTHER = Scope(ScopeKind.ROOM, "signal/generated-other")


@pytest.fixture(params=["file", "fake", "keto"])
async def backend(request, tmp_path):
    if request.param == "file":
        path = tmp_path / "policy.toml"
        path.write_text(POLICY)
        yield FileAuthorityBackend(load_policy(path))
    elif request.param == "fake":
        yield FakeAuthorityBackend()
    else:
        read, write = request.getfixturevalue("keto")
        async with aiohttp.ClientSession() as session:
            yield KetoAuthorityBackend(KetoClient(
                session=session, read_url=read, write_url=write))


async def test_scope_reads_preserve_person_and_parent_rows_and_isolation(backend):
    assert await backend.scope_relations_on(scope=ROOM) == frozenset()
    member = await backend.put_relation(
        scope=ROOM, relation="member", person_id="generated-person")
    parent = await backend.publish_containment(scope=ROOM, parent=PARENT)
    if isinstance(backend, FileAuthorityBackend):
        assert member.status is parent.status is OutcomeStatus.DENIED
        assert await backend.scope_relations_on(scope=ROOM) == frozenset()
        return
    assert member.status is parent.status is OutcomeStatus.COMPLETE
    assert await backend.scope_relations_on(scope=ROOM) == frozenset({
        ScopedRelation("member", "generated-person"),
        ScopedRelation("unit", PARENT),
    })
    assert await backend.relations_on(scope=ROOM) == frozenset({
        ("member", "generated-person"),
    })
    assert await backend.scope_relations_on(scope=OTHER) == frozenset()
    assert await backend.scope_relations_on(scope=PARENT) == frozenset()


@pytest.mark.parametrize("kind", ["fake", "keto"])
async def test_unrepresentable_subjects_make_the_whole_read_unavailable(kind, request):
    if kind == "fake":
        backend = FakeAuthorityBackend()
        await backend.put_relation(scope=ROOM, relation="member", person_id="generated-person")
        assert await backend.scope_relations_on(scope=ROOM) == frozenset({
            ScopedRelation("member", "generated-person"),
        })
        backend.tuples.add(("Room", ROOM.id, "member", ("subjectset", "Foreign", "x")))
        result = await backend.scope_relations_on(scope=ROOM)
    else:
        read, write = request.getfixturevalue("keto")
        async with aiohttp.ClientSession() as session:
            backend = KetoAuthorityBackend(KetoClient(
                session=session, read_url=read, write_url=write))
            await backend.put_relation(scope=ROOM, relation="member", person_id="generated-person")
            assert await backend.scope_relations_on(scope=ROOM) == frozenset({
                ScopedRelation("member", "generated-person"),
            })
            response = await session.put(f"{write}/admin/relation-tuples", json={
                "namespace": "Room", "object": ROOM.id, "relation": "member",
                "subject_set": {
                    "namespace": "Unit", "object": "generated-parent", "relation": "member",
                },
            })
            assert response.status == 201
            result = await backend.scope_relations_on(scope=ROOM)
    assert isinstance(result, CheckResult)
    assert result.verdict is Verdict.UNAVAILABLE


async def test_role_holders_grants_and_retraction_hold_across_backends(backend):
    """A delegated platform role: holders, the userset grant, retraction."""
    obj = role_object_id(ROOM, "stewards")
    assert await backend.role_members(scope=ROOM, role="stewards") == frozenset()
    member = await backend.put_role_member(
        scope=ROOM, role="stewards", person_id="generated-person")
    grant = await backend.put_role_grant(
        scope=ROOM, relation="grant_manage_members", role="stewards")
    if isinstance(backend, FileAuthorityBackend):
        assert member.status is grant.status is OutcomeStatus.DENIED
        assert await backend.role_members(scope=ROOM, role="stewards") == frozenset()
        return
    assert member.status is grant.status is OutcomeStatus.COMPLETE
    assert await backend.role_members(scope=ROOM, role="stewards") == frozenset(
        {"generated-person"})
    assert await backend.scope_relations_on(scope=ROOM) == frozenset({
        ScopedRelation(ROLE_OBSERVED_RELATION, RoleRef(obj)),
        ScopedRelation("grant_manage_members", RoleRef(obj, members=True)),
    })
    assert (await backend.drop_role_member(
        scope=ROOM, role="stewards", person_id="generated-person")
    ).status is OutcomeStatus.COMPLETE
    assert await backend.role_members(scope=ROOM, role="stewards") == frozenset()
    assert (await backend.drop_role_grant(
        scope=ROOM, relation="grant_manage_members", role="stewards")
    ).status is OutcomeStatus.COMPLETE
    assert (await backend.drop_role_observation(scope=ROOM, role="stewards")
    ).status is OutcomeStatus.COMPLETE
    assert await backend.scope_relations_on(scope=ROOM) == frozenset()


@pytest.mark.parametrize("kind", ["fake", "keto"])
async def test_failed_reads_are_unavailable_rather_than_empty(kind):
    if kind == "fake":
        backend = FakeAuthorityBackend()
        assert await backend.scope_relations_on(scope=ROOM) == frozenset()
        backend.down = True
        result = await backend.scope_relations_on(scope=ROOM)
    else:
        async with aiohttp.ClientSession() as session:
            backend = KetoAuthorityBackend(KetoClient(
                session=session, read_url="http://127.0.0.1:1", write_url="http://127.0.0.1:1"))
            result = await backend.scope_relations_on(scope=ROOM)
    assert isinstance(result, CheckResult)
    assert result.verdict is Verdict.UNAVAILABLE
