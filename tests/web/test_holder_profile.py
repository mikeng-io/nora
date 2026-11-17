"""`POST /app/v1/holder/profile` — the holder's own global display name.

The route authenticates only the request-bound assertion: the body carries
`operation_id` and `display_name` alone, actor and target derive from the
verified session binding, and the strict body model refuses any field that
would smuggle an actor, an account or another person (`management.md`).
"""

import hashlib
import json
import time

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    PersonLifecycle,
)
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from nora.web.holder_profile import register_profile_routes
from nora.web.management_auth import AuthenticationVerifier
from tests.unit.test_management_authentication import ISSUER, claims, signed

PERSON = "generated-person-a"
OTHER = "generated-person-b"
ACCT = Identifier("discord", "user_id", "generated-account-a")
ACCT_B = Identifier("discord", "user_id", "generated-account-b")
NEW_NAME = "Generated New Name"
OLDER_NAME = "Generated Original Name"
PROFILE = "/app/v1/holder/profile"


def headers(account, *, purpose, body, path=PROFILE, method="POST", binding=None):
    value = claims(
        account={"platform": account.platform, "kind": account.kind,
                 "value": account.value},
        purpose=purpose, method=method, path=path,
        body_sha256=hashlib.sha256(body).hexdigest(),
        authenticated_at=int(time.time()))
    if binding is not None:
        value["binding"] = binding
    return {"Authorization": "Nora-Authentication " + signed(value)}


def _binding(person, account_id, person_revision="0", account_revision="0"):
    return {"person_id": person, "person_revision": person_revision,
            "account_id": account_id, "account_revision": account_revision}


@pytest.fixture
async def surface(aiohttp_client):
    store = FakeBindingStore()
    registry = FakeIdentityRegistry()
    services = ManagedServices(
        store, FakeAuthorityBackend(), FakeTopologySource(), registry,
        FakeLinkRequests())
    for person, account_id, ident in (
            (PERSON, "generated-binding-a", ACCT),
            (OTHER, "generated-binding-b", ACCT_B)):
        await store.register(
            person=ManagedPerson(person, PersonLifecycle.ACTIVE,
                                 display_name=OLDER_NAME),
            account=ManagedAccount(account_id, person, "discord",
                                   frozenset({ident}), AccountState.BOUND))
        await registry.ensure_identity(
            external_id=person,
            traits={"display_name": OLDER_NAME, "social": True})
    app = web.Application()
    verifier = AuthenticationVerifier([ISSUER])
    register_profile_routes(
        app, profile=services.profile, verifier=verifier)
    client = await aiohttp_client(app)
    return client, store, registry, services


def _session_headers(account, person, account_id, body):
    return headers(account, purpose="session", body=body,
                   binding=_binding(person, account_id))


async def test_the_route_requires_a_verified_assertion(surface):
    client, _store, _registry, _services = surface
    response = await client.post(PROFILE)
    assert response.status == 401


async def test_a_holder_renames_their_own_person(surface):
    client, store, registry, _services = surface
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    response = await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    assert response.status == 200
    assert await response.json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == "no-store"
    person = await store.person(PERSON)
    assert person is not None and person.display_name == NEW_NAME
    assert registry.identities[PERSON]["traits"]["display_name"] == NEW_NAME
    assert registry.identities[PERSON]["traits"]["social"] is True
    assert (await store.person(OTHER)).display_name == OLDER_NAME


async def test_the_same_operation_replays_on_retry(surface):
    client, store, _registry, _services = surface
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    first = await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    second = await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    assert first.status == second.status == 200
    assert (await store.person(PERSON)).display_name == NEW_NAME


async def test_a_reused_operation_id_with_a_different_name_conflicts(surface):
    client, store, _registry, _services = surface
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    changed = json.dumps({"operation_id": "generated-op-1",
                          "display_name": "Generated Different Name"}).encode()
    response = await client.post(
        PROFILE, data=changed,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", changed))
    assert response.status == 403
    assert await response.json() == {"status": "denied",
                                   "detail": "operation-conflict"}
    assert (await store.person(PERSON)).display_name == NEW_NAME


async def test_a_body_cannot_name_an_actor_account_or_person(surface):
    """The body supplies only the name and operation id — every field that
    could select an actor or a target is refused outright."""
    client, store, _registry, _services = surface
    for smuggled in (
            {"person_id": OTHER}, {"person": OTHER}, {"account": "generated-account-b"},
            {"actor": OTHER}, {"target": OTHER}, {"holder": "generated-account-b"},
            {"scope": "generated-room"}, {"nickname": "Generated Nickname"}):
        body = json.dumps({"operation_id": "generated-op-1",
                           "display_name": NEW_NAME, **smuggled}).encode()
        response = await client.post(
            PROFILE, data=body,
            headers=_session_headers(ACCT_B, OTHER, "generated-binding-b", body))
        assert response.status == 400, smuggled
    assert (await store.person(OTHER)).display_name == OLDER_NAME
    assert await store.operation("generated-op-1") is None


async def test_malformed_bodies_refuse(surface):
    client, store, _registry, _services = surface
    for body in (
            json.dumps({}).encode(),
            json.dumps({"operation_id": "generated-op-1"}).encode(),
            json.dumps({"display_name": NEW_NAME}).encode(),
            json.dumps({"operation_id": "  ", "display_name": NEW_NAME}).encode(),
            b"not json"):
        response = await client.post(
            PROFILE, data=body,
            headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
        assert response.status == 400, body
    assert (await store.person(PERSON)).display_name == OLDER_NAME


async def test_a_forged_or_stale_binding_refuses(surface):
    """A session that names another person, another account or stale
    revisions never reaches the operation — the binding is revalidated
    live."""
    client, store, _registry, _services = surface
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    # account B asserting person A's binding — a cross-holder claim.
    forged = _binding(PERSON, "generated-binding-a")
    response = await client.post(
        PROFILE, data=body,
        headers=headers(ACCT_B, purpose="session", body=body, binding=forged))
    assert response.status == 403
    # account A asserting stale revisions.
    stale = _binding(PERSON, "generated-binding-a", person_revision="9")
    response = await client.post(
        PROFILE, data=body,
        headers=headers(ACCT, purpose="session", body=body, binding=stale))
    assert response.status == 403
    assert (await store.person(PERSON)).display_name == OLDER_NAME
    assert (await store.person(OTHER)).display_name == OLDER_NAME
    assert await store.operation("generated-op-1") is None


async def test_a_revoked_holder_refuses(surface):
    client, store, _registry, _services = surface
    await store.revoke_binding(account=ACCT)
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    response = await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    assert response.status == 403
    assert (await store.person(PERSON)).display_name == OLDER_NAME


async def test_a_kratos_outage_reports_unavailable_not_a_name_change(surface):
    client, store, registry, _services = surface
    registry.down = True
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    response = await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    # The session check itself cannot confirm the identity — unavailable,
    # never a silent name change.
    assert response.status == 503
    assert (await response.json())["status"] == "unavailable"
    assert (await store.person(PERSON)).display_name == OLDER_NAME
    assert await store.operation("generated-op-1") is None


async def test_a_mid_operation_outage_journals_and_reconciles(surface):
    """Kratos answered but the projection could not — the journaled
    operation resumes and completes."""
    client, store, _registry, services = surface

    # Break the store AFTER the kratos step: wrap set_display_name to fail
    # exactly once, like a crash between upstream write and projection.
    original = store.set_display_name

    async def fail_once(**kwargs):
        raise RuntimeError("generated projection outage")

    store.set_display_name = fail_once  # type: ignore[method-assign]
    body = json.dumps({"operation_id": "generated-op-1",
                       "display_name": NEW_NAME}).encode()
    response = await client.post(
        PROFILE, data=body,
        headers=_session_headers(ACCT, PERSON, "generated-binding-a", body))
    assert response.status == 409
    assert (await response.json())["status"] == "incomplete"
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.steps["kratos"] == "done"
    assert record.steps["projection"] == "pending"

    store.set_display_name = original  # type: ignore[method-assign]
    finished = await services.profile.reconcile()
    assert [row.operation_id for row in finished] == ["generated-op-1"]
    assert (await store.person(PERSON)).display_name == NEW_NAME
    record = await store.operation("generated-op-1")
    assert record.state is OperationJournalState.COMPLETE
