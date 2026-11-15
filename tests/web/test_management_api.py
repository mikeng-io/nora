"""Private HTTP provisioning binds the configured service, source and scope."""

from enum import Enum
from types import SimpleNamespace

import pytest
from aiohttp import web

from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
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
                               authority=_UseCase("sync_membership"))
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
