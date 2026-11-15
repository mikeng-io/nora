"""Real management HTTP derives people from request-bound sessions and rechecks scoped authority."""

import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    AuthorityBlock,
    ManagedAccount,
    ManagedPerson,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.people import AccountSessions
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from nora.web.management_api import register_management_routes
from nora.web.management_auth import AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from tests.unit.test_management_authentication import ISSUER, claims, signed
from tests.unit.test_management_authentication import PATH as EXCHANGE

PATH = "/app/v1/management/operations"
ROOM = Scope(ScopeKind.ROOM, "generated-room")
OTHER = Scope(ScopeKind.ROOM, "generated-other-room")
ACCOUNT = Identifier("discord", "user_id", "generated-account")


def operation(scope=ROOM):
    return {"operation_id": str(uuid4()), "kind": "set_membership",
        "target": {"kind": "membership", "person_id": "generated-member",
                   "scope": {"kind": scope.kind.value, "id": scope.id}},
        "payload": {"member": True}, "observed_revisions": {"authority": "observed"}}


def headers(value, body, path=PATH):
    return {"Authorization": "Nora-Authentication " + signed({**value, "path": path,
        "body_sha256": hashlib.sha256(body).hexdigest()})}


@pytest.fixture
async def surface(aiohttp_client):
    services = ManagedServices(FakeBindingStore(), FakeAuthorityBackend(),
        FakeTopologySource(rooms={ROOM.id: ("generated-unit", False),
                                  OTHER.id: ("generated-other-unit", False)},
                           units={"generated-unit": "generated-workspace",
                                  "generated-other-unit": "generated-workspace"},
                           workspaces={"generated-workspace"}),
        FakeIdentityRegistry(), FakeLinkRequests())
    member = Identifier("discord", "user_id", "other-account")
    for person, identifier in (("generated-operator", ACCOUNT), ("generated-member", member)):
        await services.bindings.register(
            person=ManagedPerson(person, PersonLifecycle.ACTIVE),
            account=ManagedAccount("binding-" + person, person, "discord",
                                   frozenset({identifier}), AccountState.BOUND))
        await services.registry.ensure_identity(external_id=person, traits={})
    await services.backend.put_relation(scope=ROOM, relation="admin",
                                        person_id="generated-operator")
    app = web.Application()
    verifier = AuthenticationVerifier([ISSUER])
    register_session_routes(app, sessions=AccountSessions(services.bindings, services.registry),
                             verifier=verifier)
    register_management_routes(app, services=services, credentials=(), authentication=verifier)
    client = await aiohttp_client(app)
    login = claims()
    body = b"{}"
    response = await client.post(EXCHANGE, data=body, headers=headers(login, body, EXCHANGE))
    assert response.status == 200
    login.update(purpose="session", binding=(await response.json())["binding"])
    return client, services, login


async def send(surface, value=None, *, proof=None):
    client, _, authentication = surface
    body = json.dumps(operation() if value is None else value).encode()
    return await client.post(PATH, data=body, headers=headers(proof or authentication, body))


async def test_scoped_operator_can_update_membership_and_retry_the_exact_effect(surface):
    _, services, _ = surface
    body = operation()
    assert (await send(surface, body)).status == 200
    assert (await send(surface, body)).status == 200
    members = await services.bindings.members_of(ROOM)
    assert {member.person_id for member in members} == {"generated-member"}
    assert (await send(surface, operation(OTHER))).status == 403
    assert await services.bindings.members_of(OTHER) == ()


@pytest.mark.parametrize("failure,expected", [
    ("permission-revoked", 403), ("authority-down", 503), ("identity-held", 503),
    ("binding-revised", 403), ("identity-disabled", 403), ("revocation-incomplete", 503),
])
async def test_privileged_request_revalidates_live_session_and_authority(
    surface, failure, expected,
):
    _, services, _ = surface
    if failure == "permission-revoked":
        await services.backend.drop_relation(scope=ROOM, relation="admin",
                                             person_id="generated-operator")
    elif failure == "authority-down":
        services.backend.down = True
    elif failure == "identity-held":
        await services.bindings.hold_identity(person_id="generated-operator",
                                              operation_id="generated-publication")
    elif failure == "binding-revised":
        account = services.bindings.accounts["binding-generated-operator"]
        services.bindings.accounts[account.account_id] = replace(
            account, revision=account.revision + 1)
    elif failure == "identity-disabled":
        await services.registry.set_state(external_id="generated-operator", active=False)
    else:
        await services.bindings.block_authority(AuthorityBlock(
            "generated-operator", ROOM, "incomplete-revocation", "generated-revocation"))
    assert (await send(surface)).status == expected
    assert await services.bindings.members_of(ROOM) == ()


async def test_request_body_cannot_nominate_an_actor_or_convert_login_into_management(surface):
    body = {**operation(), "actor": {"kind": "installation", "person_id": "someone-else"}}
    assert (await send(surface, body)).status == 400
    _, _, authentication = surface
    fresh = {key: value for key, value in authentication.items() if key != "binding"}
    fresh["purpose"] = "sign_in"
    assert (await send(surface, proof=fresh)).status == 401


async def test_registration_uses_the_same_current_scoped_operator_authority(surface):
    _, services, _ = surface
    account = {"platform": "discord", "kind": "user_id", "value": "generated-new-account"}
    body = {"operation_id": str(uuid4()), "kind": "register_account",
        "target": {"kind": "account", "account": account},
        "payload": {"person_id": "generated-new-person", "identifiers": [account],
                    "scope": {"kind": "room", "id": ROOM.id}},
        "observed_revisions": {"ownership": "0", "authority": "observed"}}
    assert (await send(surface, body)).status == 200
    bound = await services.bindings.account_for("discord", "user_id", account["value"])
    assert bound.person_id == "generated-new-person"
    body["operation_id"] = str(uuid4())
    body["payload"]["scope"]["id"] = OTHER.id
    assert (await send(surface, body)).status == 403


async def test_session_change_while_waiting_for_the_write_gate_prevents_a_privileged_write(surface):
    import asyncio

    from nora.domain.managed import OutcomeStatus
    from nora.people import HumanManagement
    from nora.web.management_request import decode_management_request

    _, services, proof = surface
    body = json.dumps(operation()).encode()
    token = headers(proof, body)["Authorization"].removeprefix("Nora-Authentication ")
    authentication = AuthenticationVerifier([ISSUER]).verify(
        token, method="POST", path=PATH, body=body)
    typed = decode_management_request(body, operator_person=proof["binding"]["person_id"])
    invocation = HumanManagement(services.bindings, services.registry,
                                 services.registration, services.authority)
    async with services.bindings.mutation():
        waiting = asyncio.create_task(invocation.apply(typed, authentication))
        await asyncio.sleep(0)
        assert not waiting.done()
        account = services.bindings.accounts["binding-generated-operator"]
        services.bindings.accounts[account.account_id] = replace(
            account, revision=account.revision + 1)
    result = await asyncio.wait_for(waiting, timeout=2)
    assert result.status is OutcomeStatus.DENIED
    assert await services.bindings.members_of(ROOM) == ()
