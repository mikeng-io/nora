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
    OutcomeStatus,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.domain.models import NormalizedMessage
from nora.people import AccountSessions, RoomManagement, RoomRequest
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
from tests.unit.test_scoped_profile import _ScopedReadFault

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
async def surface(aiohttp_client, request):
    mode = getattr(request, "param", None)
    store = FakeBindingStore() if mode is None else _ScopedReadFault(mode)
    services = ManagedServices(store, FakeAuthorityBackend(),
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
                                 services.registration, services.authority,
                                 services.scoped_profile)
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


def profile_operation(scope=ROOM, *, person_id="generated-member",
                      nickname="Generated Room Nickname", op_id=None):
    return {"operation_id": op_id or str(uuid4()), "kind": "update_profile",
        "target": {"kind": "person", "person_id": person_id},
        "payload": {"nickname": nickname,
                    "scope": {"kind": scope.kind.value, "id": scope.id}},
        "observed_revisions": {"identity": "0", "authority": "observed"}}


async def test_scoped_nickname_update_uses_the_same_authenticated_gate(surface):
    """A scoped ProfileEdit rides the same retained-session + scoped
    `manage_members` gate as every human operation — the write lands as
    Nora-owned metadata, the global name is untouched, a sibling-scope
    operator is refused with nothing written."""
    _, services, _ = surface
    body = profile_operation()
    assert (await send(surface, body)).status == 200
    # The exact request replays its committed outcome.
    assert (await send(surface, body)).status == 200
    assert (await services.bindings.scoped_name_for(
        person_id="generated-member", scope=ROOM)) \
        == "Generated Room Nickname"
    member = await services.bindings.person("generated-member")
    assert member is not None and member.display_name == ""
    # Operator admin on ROOM confers nothing on the sibling room.
    assert (await send(surface, profile_operation(OTHER))).status == 403
    assert (await services.bindings.scoped_name_for(
        person_id="generated-member", scope=OTHER)) is None


@pytest.mark.parametrize("failure,expected", [
    ("permission-revoked", 403), ("authority-down", 503),
    ("identity-held", 503), ("unknown-person", 403),
    ("unknown-scope", 403), ("global-payload", 400),
])
async def test_profile_update_revalidates_the_same_boundaries(
    surface, failure, expected,
):
    _, services, _ = surface
    body = profile_operation()
    if failure == "permission-revoked":
        await services.backend.drop_relation(
            scope=ROOM, relation="admin", person_id="generated-operator")
    elif failure == "authority-down":
        services.backend.down = True
    elif failure == "identity-held":
        await services.bindings.hold_identity(
            person_id="generated-member",
            operation_id="generated-publication")
    elif failure == "unknown-person":
        body = profile_operation(person_id="generated-absent")
    elif failure == "unknown-scope":
        body = profile_operation(Scope(ScopeKind.ROOM, "generated-missing"))
    else:
        body = {**profile_operation(), "payload": {"display_name": "Sneaked"}}
    assert (await send(surface, body)).status == expected
    assert services.bindings.scoped_names == {}
    member = await services.bindings.person("generated-member")
    assert member is not None and member.display_name == ""


async def test_profile_update_needs_the_retained_session(surface):
    client, _, _ = surface
    body = json.dumps(profile_operation()).encode()
    # No Nora-Authentication header at all, and a sign-in proof with no
    # retained binding: neither reaches the operation.
    assert (await client.post(PATH, data=body)).status == 401
    fresh = dict(claims())
    fresh["purpose"] = "sign_in"
    assert (await client.post(
        PATH, data=body, headers=headers(fresh, body))).status == 401


@pytest.mark.parametrize("surface", ["mismatch", "exception"], indirect=True)
async def test_scoped_profile_http_reports_a_committed_partial_change(surface):
    _, services, _ = surface
    body = profile_operation()
    response = await send(surface, body)
    assert response.status == 409
    assert (await response.json())["status"] == "incomplete"
    store = services.bindings
    record = await store.operation(body["operation_id"])
    assert record.steps["profile"] == "done"
    store.fault = False
    assert await store.scoped_name_for(
        person_id="generated-member", scope=ROOM) == body["payload"]["nickname"]
    assert await services.scoped_profile.reconcile()
    assert (await send(surface, body)).status == 200
    assert store.name_writes == 1


async def test_a_session_cannot_mint_installation_authority(surface):
    """The session path hardcodes OPERATOR: a well-formed
    `suspend_identity` body under a valid session is refused at decode —
    the authenticated session cannot nominate installation authority."""
    body = {"operation_id": str(uuid4()), "kind": "suspend_identity",
        "target": {"kind": "person", "person_id": "generated-member"},
        "payload": {"reason": "generated-reason"},
        "observed_revisions": {"identity": "r-1", "authority": "r-1"}}
    assert (await send(surface, body)).status == 400


async def test_in_room_and_web_app_share_one_typed_actor_and_scope(surface):
    """One membership change through the in-room command and the Web App
    session is handed to the shared gate as the same typed `Operation`:
    `Actor(OPERATOR, <verified person>, <the room the caller is actually
    in>)`. The gate's committed operations — recorded at
    `AuthorityService._apply`, the call `HumanManagement.apply` reaches for
    both entries — carry identical actor and scope, and the journaled
    effects are identical. A different authenticated person commits under
    ITS actor, so the capture is not a constant. Neither ingress can
    supply actor, room or credential: the session's binding claim and the
    message's verified provenance fill them."""
    client, services, proof = surface
    person = proof["binding"]["person_id"]  # generated-operator
    room = Scope(ScopeKind.ROOM, room_scope_id("discord", "generated-conv"))
    services.topology.rooms[room.id] = ("generated-unit", False)
    await services.backend.put_relation(
        scope=room, relation="admin", person_id=person)

    # Record the exact `Operation` each real request hands the shared gate.
    committed = []
    apply = services.authority._apply
    async def record(op):
        committed.append(op)
        return await apply(op)
    services.authority._apply = record

    # Web App ingress: the session's binding claim types the actor and
    # the body's target types the scope — the request supplies neither.
    body = {**operation(room), "operation_id": "generated-shared-web-op"}
    web_response = await send(surface, body)

    # In-room ingress: the verified message types the same actor/scope.
    message = NormalizedMessage(
        platform="discord", conversation_id="generated-conv",
        conversation_type="group", sender_id=ACCOUNT.value,
        sender_name=None, timestamp=1_700_000_000_000,
        body="!!!member add @other-account", raw={},
        mentions=frozenset({"other-account"}), quote=None,
        message_id="generated-shared-msg", edit_of=None, touch=None)
    management = RoomManagement(services)
    request = management.parse(message)
    assert request == RoomRequest(
        kind="add", reference="other-account", reference_kind="mention")
    outcome = await management.invoke(message, request)

    # What the gate actually received from each entry — not a re-decode.
    assert [op.actor for op in committed] == [
        Actor(ActorKind.OPERATOR, person_id=person, scope=room)] * 2
    web_op, room_op = committed
    assert web_op.target.scope == room_op.target.scope == room
    assert web_response.status == 200
    assert outcome.status is OutcomeStatus.COMPLETE

    web_record = await services.bindings.operation("generated-shared-web-op")
    room_record = await services.bindings.operation(
        "room:discord:generated-conv:generated-shared-msg")
    assert web_record is not None and room_record is not None
    assert web_record.kind == room_record.kind == "set_membership"
    assert dict(web_record.snapshot) == dict(room_record.snapshot)

    # Discriminating negative: the same body under a second bound person's
    # session commits under ITS actor — a dropped or defaulted actor
    # cannot produce a different captured person.
    second = "generated-operator-2"
    await services.bindings.register(
        person=ManagedPerson(second, PersonLifecycle.ACTIVE),
        account=ManagedAccount("binding-" + second, second, "discord",
            frozenset({Identifier("discord", "user_id", "generated-account-2")}),
            AccountState.BOUND))
    await services.registry.ensure_identity(external_id=second, traits={})
    await services.backend.put_relation(
        scope=room, relation="admin", person_id=second)
    login2 = claims(account={"platform": "discord", "kind": "user_id",
                             "value": "generated-account-2"})
    empty = b"{}"
    exchange = await client.post(EXCHANGE, data=empty,
        headers=headers(login2, empty, EXCHANGE))
    assert exchange.status == 200
    login2.update(purpose="session", binding=(await exchange.json())["binding"])
    body2 = json.dumps(
        {**operation(room), "operation_id": "generated-second-web-op"}).encode()
    assert (await client.post(
        PATH, data=body2, headers=headers(login2, body2))).status == 200
    assert committed[2].actor == Actor(
        ActorKind.OPERATOR, person_id=second, scope=room)
    assert committed[2].actor != committed[0].actor
