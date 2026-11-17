"""Private HTTP provisioning binds the configured service, source and scope."""

from dataclasses import replace
from enum import Enum
from types import SimpleNamespace

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.testing.managed import FakeBindingStore, FakeIdentityRegistry, FakeTopologySource
from nora.web.management_api import ProvisioningCredential, register_management_routes

TOKEN = "generated-provisioning-credential"  # noqa: S105
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PATH = "/app/v1/management/operations"
ROOM = Scope(ScopeKind.ROOM, "synthetic-room")
ACTOR = Actor(ActorKind.SERVICE, service_id="synthetic-bridge", sources={"discord"})


class _Status(Enum):
    COMPLETE = "complete"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    INCOMPLETE = "incomplete"


class _UseCase:
    """A transport collaborator: reject misrouting, retain the typed request."""

    def __init__(self, kind):
        self.kind = kind
        self.calls = []
        self.status = _Status.COMPLETE

    async def apply(self, operation):
        assert operation.kind == self.kind
        self.calls.append(operation)
        return SimpleNamespace(status=self.status, detail="generated-result")


def registration():
    account = {"platform": "discord", "kind": "user_id", "value": "synthetic-member"}
    return {
        "operation_id": "synthetic-operation", "kind": "register_account",
        "target": {"kind": "account", "account": account},
        "payload": {"person_id": "synthetic-person", "identifiers": [account],
                    "scope": {"kind": "room", "id": ROOM.id}},
        "observed_revisions": {"ownership": "ownership-1", "authority": "authority-1"},
    }


def credential(**changes):
    values = {"actor": ACTOR, "token": TOKEN, "scopes": {ROOM}}
    values.update(changes)
    return ProvisioningCredential(**values)


@pytest.fixture
async def surface(aiohttp_client):
    services = SimpleNamespace(registration=_UseCase("register_account"),
                               authority=_UseCase("sync_membership"),
                               bindings=FakeBindingStore(), registry=FakeIdentityRegistry(),
                               topology=FakeTopologySource(rooms={ROOM.id: ("unit", False)}))
    app = web.Application()
    register_management_routes(app, services=services, credentials=(credential(),))
    return await aiohttp_client(app), services


async def test_authenticated_registration_receives_the_server_actor(surface):
    client, services = surface
    response = await client.post(PATH, headers=AUTH, json=registration())
    assert response.status == 200
    assert await response.json() == {"status": "complete", "detail": "generated-result"}
    assert response.headers["Cache-Control"] == "no-store"
    assert services.registration.calls[0].actor is ACTOR
    assert services.authority.calls == []


async def seed_identity(services):
    account = Identifier(platform="discord", kind="user_id", value="generated-holder")
    await services.bindings.register(
        person=ManagedPerson("generated-person", PersonLifecycle.ACTIVE),
        account=ManagedAccount("generated-account", "generated-person", "discord",
                               frozenset({account}), AccountState.BOUND),
    )
    await services.registry.ensure_identity(external_id="generated-person", traits={})
    return {"account": {"platform": account.platform, "kind": account.kind, "value": account.value},
            "scope": {"kind": ROOM.kind.value, "id": ROOM.id}}


async def test_resolution_returns_only_the_active_canonical_person(surface):
    client, services = surface
    query = await seed_identity(services)
    response = await client.post(PATH.replace("operations", "accounts/resolve"),
                                 headers=AUTH, json=query)
    assert response.status == 200
    assert await response.json() == {"status": "ok", "person_id": "generated-person"}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("failure,status,result", [
    ("unknown", 404, "nothing_found"), ("revoked", 404, "nothing_found"),
    ("suspended", 403, "denied"), ("inactive", 403, "denied"),
    ("bindings-down", 503, "unavailable"), ("registry-down", 503, "unavailable"),
    ("registry-missing", 503, "unavailable"), ("topology-down", 503, "unavailable"),
    ("scope-removed", 403, "denied"),
])
async def test_resolution_distinguishes_absence_refusal_and_failure(
    surface, failure, status, result,
):
    client, services = surface
    query = await seed_identity(services)
    if failure == "unknown":
        query["account"]["value"] = "generated-unknown"
    elif failure == "revoked":
        account = services.bindings.accounts["generated-account"]
        services.bindings.accounts[account.account_id] = replace(
            account, state=AccountState.REVOKED,
        )
    elif failure == "suspended":
        person = services.bindings.people["generated-person"]
        services.bindings.people[person.person_id] = replace(
            person, lifecycle=PersonLifecycle.SUSPENDED,
        )
    elif failure == "inactive":
        await services.registry.set_state(external_id="generated-person", active=False)
    elif failure == "registry-missing":
        services.registry.identities.clear()
    elif failure == "scope-removed":
        services.topology.rooms.clear()
    else:
        getattr(services, failure.removesuffix("-down")).down = True
    response = await client.post(PATH.replace("operations", "accounts/resolve"),
                                 headers=AUTH, json=query)
    assert response.status == status
    assert await response.json() == {"status": result}


@pytest.mark.parametrize("change,status", [
    ("auth", 401), ("source", 403), ("scope", 403), ("actor", 400),
])
async def test_resolution_cannot_supply_authority_or_escape_credential(surface, change, status):
    client, services = surface
    query = await seed_identity(services)
    headers = AUTH
    if change == "auth":
        headers = {}
    elif change == "source":
        query["account"] = {"platform": "telegram", "kind": "user_id", "value": "other"}
    elif change == "scope":
        query["scope"]["id"] = "other-room"
    else:
        query["actor"] = {"kind": "installation"}
    response = await client.post(PATH.replace("operations", "accounts/resolve"),
                                 headers=headers, json=query)
    assert response.status == status


async def test_mutation_rechecks_scope_after_startup(surface):
    client, services = surface
    services.topology.rooms.clear()
    response = await client.post(PATH, headers=AUTH, json=registration())
    assert response.status == 403
    assert not services.registration.calls


async def test_bulk_roster_uses_one_typed_operation_and_preserves_completeness(surface):
    client, services = surface
    body = {
        "operation_id": "synthetic-roster", "kind": "sync_membership",
        "target": {"kind": "scope", "scope": {"kind": "room", "id": ROOM.id}},
        "payload": {"members": [
            {"platform": "discord", "kind": "user_id", "value": f"synthetic-member-{i}"}
            for i in range(125)
        ], "complete": False},
        "observed_revisions": {"authority": "authority-1"},
    }
    response = await client.post(PATH, headers=AUTH, json=body)
    assert response.status == 200
    operation, = services.authority.calls
    assert operation.actor is ACTOR
    assert len(operation.payload.members) == 125
    assert operation.payload.complete is False
    assert services.registration.calls == []


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", TOKEN, "bearer " + TOKEN])
async def test_authentication_precedes_even_malformed_body(surface, authorization):
    client, services = surface
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await client.post(PATH, headers=headers, data=b"not-json")
    assert response.status == 401
    assert await response.json() == {"status": "denied"}
    assert not services.registration.calls and not services.authority.calls


async def test_a_valid_effect_without_credentials_cannot_reach_the_use_case(surface):
    client, services = surface
    response = await client.post(PATH, json=registration())
    assert response.status == 401
    assert not services.registration.calls and not services.authority.calls


@pytest.mark.parametrize("location", ["body", "target", "payload"])
async def test_request_cannot_nominate_an_actor_at_any_level(surface, location):
    client, services = surface
    body = registration()
    destination = body if location == "body" else body[location]
    destination["actor"] = {"kind": "installation", "person_id": "forged"}
    response = await client.post(PATH, headers=AUTH, json=body)
    assert response.status == 400
    assert not services.registration.calls and not services.authority.calls


async def test_credential_scope_is_exact_and_source_is_not_caller_controlled(surface):
    client, services = surface
    body = registration()
    body["payload"]["scope"]["id"] = "another-room"
    response = await client.post(PATH, headers=AUTH, json=body)
    assert response.status == 403
    body = registration()
    body["target"]["account"]["platform"] = "telegram"
    response = await client.post(PATH, headers=AUTH, json=body)
    assert response.status == 400
    assert not services.registration.calls and not services.authority.calls


async def test_service_token_never_becomes_operator_or_holder_authority(surface):
    client, services = surface
    body = registration()
    body.update(kind="delegate", target={
        "kind": "scope", "scope": {"kind": "room", "id": ROOM.id},
    }, payload={"delegatee": "synthetic-admin", "permits": ["delegate"]})
    response = await client.post(PATH, headers=AUTH, json=body)
    assert response.status == 400
    assert not services.registration.calls and not services.authority.calls


async def test_the_provisioning_credential_never_reaches_a_response_body(surface):
    """The bearer token authenticates but is never echoed: success, refusal,
    outage, malformed and resolve replies carry no credential."""
    client, services = surface
    query = await seed_identity(services)
    responses = [
        await client.post(PATH, headers=AUTH, json=registration()),
        await client.post(PATH, json=registration()),
        await client.post(PATH, headers=AUTH, data=b"not-json"),
        await client.post(PATH.replace("operations", "accounts/resolve"),
                          headers=AUTH, json=query),
    ]
    services.registration.status = _Status.UNAVAILABLE
    responses.append(
        await client.post(PATH, headers=AUTH, json=registration()))
    for response in responses:
        assert TOKEN not in await response.text()


@pytest.mark.parametrize("status,http", [
    (_Status.DENIED, 403), (_Status.UNAVAILABLE, 503), (_Status.INCOMPLETE, 409),
])
async def test_refusal_outage_and_partial_commit_never_become_success(surface, status, http):
    client, services = surface
    services.registration.status = status
    response = await client.post(PATH, headers=AUTH, json=registration())
    assert response.status == http
    assert (await response.json())["status"] == status.value
    assert response.headers["Cache-Control"] == "no-store"


def test_credential_is_immutable_and_its_repr_does_not_reveal_the_token():
    scopes = {ROOM}
    value = credential(scopes=scopes)
    scopes.clear()
    assert value.scopes == frozenset({ROOM})
    assert TOKEN not in repr(value)


@pytest.mark.parametrize("changes", [
    {"token": ""}, {"token": " "}, {"scopes": set()},
    {"actor": Actor(ActorKind.INSTALLATION, person_id="synthetic-admin")},
])
def test_empty_or_non_service_credential_is_refused(changes):
    with pytest.raises(ValueError):
        credential(**changes)


@pytest.mark.parametrize("credentials", [(), (credential(), credential()), (
    credential(), credential(token="other-generated-token"),  # noqa: S106
)])
def test_mount_refuses_ambiguous_or_empty_authentication_configuration(credentials):
    with pytest.raises(ValueError):
        register_management_routes(web.Application(), services=None, credentials=credentials)
