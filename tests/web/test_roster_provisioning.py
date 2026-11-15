"""Bulk onboarding through real HTTP and managed services, with generated accounts."""

import pytest
from aiohttp import web

from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from nora.web.management_api import ProvisioningCredential, register_management_routes

PATH = "/app/v1/management/rosters"
UNIT = Scope(ScopeKind.UNIT, "generated-unit")
ROOM = Scope(ScopeKind.ROOM, "generated-room")
AUTH = {"Authorization": "Bearer generated-roster-secret"}


def account(number, classification="person"):
    return {"account": {"platform": "discord", "kind": "user_id", "value": f"generated-{number}"},
            "classification": classification}


def roster(key="generated-snapshot", members=None, **kwargs):
    return {"operation_id": key, "collection": "generated-guild", "platform": "discord",
        "scope": {"kind": "unit", "id": UNIT.id}, "audience": "collection",
        "members": [account(n) for n in range(150)] if members is None else members,
        "complete": True, **kwargs}


@pytest.fixture
async def surface(aiohttp_client):
    services = ManagedServices(FakeBindingStore(), FakeAuthorityBackend(),
        FakeTopologySource(rooms={ROOM.id: (UNIT.id, False)},
            units={UNIT.id: "generated-workspace"}, workspaces={"generated-workspace"}),
        FakeIdentityRegistry(), FakeLinkRequests())
    app = web.Application()
    register_management_routes(app, services=services, credentials=(ProvisioningCredential(
        Actor(ActorKind.SERVICE, service_id="generated-bridge", sources={"discord"}),
        "generated-roster-secret", frozenset({UNIT, ROOM})),))
    return await aiohttp_client(app), services


async def test_one_roster_registers_over_a_hundred_people_without_duplicate_on_retry(surface):
    client, services = surface
    response = await client.post(PATH, json=roster(), headers=AUTH)
    assert response.status == 200, await response.text()
    assert response.headers["Cache-Control"] == "no-store"
    assert len(services.bindings.people) == 150
    assert len(services.registry.identities) == 150
    memberships = await services.bindings.members_of(UNIT)
    assert len(memberships) == 150
    assert all(m.sources == {'sync:["generated-bridge","generated-guild"]'} for m in memberships)
    assert not (await services.authority.check(scope=ROOM, person_id=memberships[0].person_id,
                                             permit="view")).allowed
    assert (await client.post(PATH, json=roster(), headers=AUTH)).status == 200
    assert len(services.bindings.people) == 150
    assert (await client.post(PATH, json=roster(members=[]), headers=AUTH)).status == 403


async def test_partial_roster_bots_unknowns_and_complete_departure_are_distinct(surface):
    client, services = surface
    assert (await client.post(PATH, json=roster(members=[account(1), account(2, "bot")]),
                              headers=AUTH)).status == 200
    assert len(services.bindings.people) == 1
    partial = roster("generated-partial", [account(3, "unknown")], complete=False)
    assert (await client.post(PATH, json=partial, headers=AUTH)).status == 200
    member = (await services.bindings.members_of(UNIT))[0]
    assert member.state.value == "member"
    unknown = roster("generated-unknown", [account(4, "unknown")])
    assert (await client.post(PATH, json=unknown, headers=AUTH)).status == 503
    assert (await services.bindings.members_of(UNIT))[0].state.value == "member"
    departed = await client.post(PATH, json=roster("generated-departure", []), headers=AUTH)
    assert departed.status == 200
    assert await services.bindings.members_of(UNIT) == ()
    assert (await services.bindings.memberships_for(member.person_id))[0].state.value == "left"


async def test_registry_failure_leaves_no_membership_and_retry_repairs_original_person(surface):
    client, services = surface
    services.registry.down = True
    request = roster(members=[account(1)])
    assert (await client.post(PATH, json=request, headers=AUTH)).status == 409
    original = set(services.bindings.people)
    assert len(original) == 1
    assert not await services.bindings.members_of(UNIT)
    services.registry.down = False
    assert (await client.post(PATH, json=request, headers=AUTH)).status == 200
    assert set(services.bindings.people) == original
    assert len(await services.bindings.members_of(UNIT)) == 1
    assert len(services.registry.identities) == 1


@pytest.mark.parametrize("change,expected", [
    ({"scope": {"kind": "room", "id": ROOM.id}}, 403),
    ({"scope": {"kind": "unit", "id": "unapproved-unit"}}, 403),
    ({"platform": "signal", "members": []}, 403),
    ({"actor": {"kind": "installation"}}, 400),
])
async def test_source_scope_and_collection_audience_cannot_be_enlarged(surface, change, expected):
    client, services = surface
    assert (await client.post(PATH, json=roster(**change), headers=AUTH)).status == expected
    assert not services.bindings.people


async def test_missing_service_credential_and_conflicting_classifications_refuse(surface):
    client, services = surface
    assert (await client.post(PATH, json=roster())).status == 401
    assert (await client.post(PATH, json=roster(members=[account(1), account(1, "bot")]),
                              headers=AUTH)).status == 400
    assert not services.bindings.people


async def test_revoked_binding_is_never_re_registered_by_a_later_snapshot(surface):
    from nora.domain.identity import Identifier

    client, services = surface
    body = roster(members=[account(1)])
    assert (await client.post(PATH, json=body, headers=AUTH)).status == 200
    original_people = set(services.bindings.people)
    await services.bindings.revoke_binding(
        account=Identifier("discord", "user_id", "generated-1"))
    body["operation_id"] = "generated-after-revocation"
    assert (await client.post(PATH, json=body, headers=AUTH)).status == 409
    assert set(services.bindings.people) == original_people
    assert await services.bindings.account_for("discord", "user_id", "generated-1") is None
