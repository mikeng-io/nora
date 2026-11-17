"""The holder link routes authenticate the request-bound assertion and
never the body — actor, account and proof come only from the verified
claim and the revalidated binding (`account-linking.md`)."""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.people import HolderLinkService
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from nora.testing.managed_authority import FakeAuthorityBackend
from nora.web.holder_links import register_holder_routes
from nora.web.management_auth import AuthenticationVerifier
from tests.unit.test_management_authentication import ISSUER, claims, signed

PERSON_A = "generated-person-a"
PERSON_B = "generated-person-b"
PERSON_C = "generated-person-c"
ACCT_A = Identifier("discord", "user_id", "generated-account-a")
ACCT_B = Identifier("discord", "user_id", "generated-account-b")
ACCT_C = Identifier("discord", "user_id", "generated-account-c")
ROOM = Scope(ScopeKind.ROOM, "generated-room")
REQUESTS = "/app/v1/holder/link-requests"
UNLINK = "/app/v1/holder/unlink"


def headers(account, *, purpose, body, path, method="POST", binding=None):
    value = claims(
        account={"platform": account.platform, "kind": account.kind,
                 "value": account.value},
        purpose=purpose, method=method, path=path,
        body_sha256=hashlib.sha256(body).hexdigest(),
        authenticated_at=int(time.time()))
    if binding is not None:
        value["binding"] = binding
    return {"Authorization": "Nora-Authentication " + signed(value)}


def _binding(person, account_id):
    return {"person_id": person, "person_revision": "0",
            "account_id": account_id, "account_revision": "0"}


@pytest.fixture
async def surface(aiohttp_client):
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: ("generated-unit", False)},
        units={"generated-unit": "generated-workspace"},
        workspaces={"generated-workspace"})
    registry = FakeIdentityRegistry()
    links = FakeLinkRequests()
    services = ManagedServices(store, backend, topology, registry, links)
    for person, account_id, ident in (
            (PERSON_A, "binding-a", ACCT_A),
            (PERSON_B, "binding-b", ACCT_B),
            (PERSON_C, "binding-c", ACCT_C)):
        await store.register(
            person=ManagedPerson(person, PersonLifecycle.ACTIVE),
            account=ManagedAccount(account_id, person, "discord",
                                   frozenset({ident}), AccountState.BOUND))
        await registry.ensure_identity(external_id=person, traits={})
    # A mutable service clock floored to whole seconds — the assertion's
    # `authenticated_at` is whole-second too, so a same-second decision still
    # sits inside [created_at, now]; a decision moves the clock forward.
    now = [datetime.now(UTC).replace(microsecond=0)]
    holder = HolderLinkService(
        store, registry, links, services.publication, services.registration,
        clock=lambda: now[0])
    app = web.Application()
    verifier = AuthenticationVerifier([ISSUER])
    register_holder_routes(app, holder=holder, verifier=verifier)
    client = await aiohttp_client(app)
    return client, store, registry, links, now


async def test_every_route_requires_a_verified_assertion(surface):
    client, _store, _registry, _links, _now = surface
    for method, path in (
            ("POST", REQUESTS), ("GET", f"{REQUESTS}/x"),
            ("POST", f"{REQUESTS}/x/decisions"), ("POST", UNLINK)):
        response = await client.request(method, path)
        assert response.status == 401


async def test_create_and_per_side_views_expose_only_the_callers_challenge(surface):
    client, _store, _registry, _links, _now = surface
    body = json.dumps({"request_id": "generated-request",
                       "receiving": {"platform": "discord", "kind": "user_id",
                                     "value": ACCT_B.value},
                       "surviving_person": PERSON_A}).encode()
    response = await client.post(
        REQUESTS, data=body,
        headers=headers(ACCT_A, purpose="session", body=body, path=REQUESTS,
                        binding=_binding(PERSON_A, "binding-a")))
    assert response.status == 200
    payload = await response.json()
    request = payload["request"]
    assert request["side"] == "initiating" and request["state"] == "pending"
    assert request["surviving_person"] == PERSON_A
    assert request["pair"]["receiving"]["value"] == ACCT_B.value
    initiating_nonce = request["challenge"]["nonce"]

    path = f"{REQUESTS}/generated-request"
    received = await client.get(
        path, headers=headers(ACCT_B, purpose="session", body=b"", path=path,
                              method="GET", binding=_binding(PERSON_B, "binding-b")))
    assert received.status == 200
    other = (await received.json())["request"]
    assert other["side"] == "receiving"
    assert other["challenge"]["nonce"] != initiating_nonce

    foreign = await client.get(
        path, headers=headers(ACCT_C, purpose="session", body=b"", path=path,
                              method="GET", binding=_binding(PERSON_C, "binding-c")))
    assert foreign.status == 403


async def test_the_body_can_never_supply_an_actor_or_proof(surface):
    client, _store, _registry, _links, _now = surface
    body = json.dumps({"request_id": "generated-request",
                       "receiving": {"platform": "discord", "kind": "user_id",
                                     "value": ACCT_B.value},
                       "surviving_person": PERSON_A,
                       "actor": {"person_id": PERSON_B}}).encode()
    response = await client.post(
        REQUESTS, data=body,
        headers=headers(ACCT_A, purpose="session", body=body, path=REQUESTS,
                        binding=_binding(PERSON_A, "binding-a")))
    assert response.status == 400


async def test_a_session_cannot_decide_and_a_wrong_nonce_is_denied(surface):
    client, _store, _registry, _links, now = surface
    body = json.dumps({"request_id": "generated-request",
                       "receiving": {"platform": "discord", "kind": "user_id",
                                     "value": ACCT_B.value},
                       "surviving_person": PERSON_A}).encode()
    await client.post(
        REQUESTS, data=body,
        headers=headers(ACCT_A, purpose="session", body=body, path=REQUESTS,
                        binding=_binding(PERSON_A, "binding-a")))
    path = f"{REQUESTS}/generated-request/decisions"
    decision = json.dumps({"operation_id": "op-1", "action": "approve",
                           "nonce": "whatever"}).encode()

    retained = await client.post(
        path, data=decision,
        headers=headers(ACCT_A, purpose="session", body=decision, path=path,
                        binding=_binding(PERSON_A, "binding-a")))
    assert retained.status == 401

    # The sign-in is otherwise valid — only the nonce fails the proof. A
    # fresh provider authentication is real-time (`iat - authenticated_at`
    # <= 60s), so the service clock must sit just past real "now".
    now[0] = datetime.now(UTC) + timedelta(seconds=5)
    wrong = await client.post(
        path, data=decision,
        headers=headers(ACCT_A, purpose="sign_in", body=decision, path=path))
    assert wrong.status == 403


async def test_both_holders_approve_and_the_request_commits(surface):
    client, store, _registry, _links, now = surface
    body = json.dumps({"request_id": "generated-request",
                       "receiving": {"platform": "discord", "kind": "user_id",
                                     "value": ACCT_B.value},
                       "surviving_person": PERSON_A}).encode()
    created = await client.post(
        REQUESTS, data=body,
        headers=headers(ACCT_A, purpose="session", body=body, path=REQUESTS,
                        binding=_binding(PERSON_A, "binding-a")))
    request = (await created.json())["request"]
    view_path = f"{REQUESTS}/generated-request"
    received = await client.get(
        view_path,
        headers=headers(ACCT_B, purpose="session", body=b"", path=view_path,
                        method="GET", binding=_binding(PERSON_B, "binding-b")))
    nonces = {
        "a": request["challenge"]["nonce"],
        "b": (await received.json())["request"]["challenge"]["nonce"],
    }
    path = f"{REQUESTS}/generated-request/decisions"
    now[0] = datetime.now(UTC) + timedelta(seconds=5)

    for account, nonce in ((ACCT_A, nonces["a"]), (ACCT_B, nonces["b"])):
        decision = json.dumps({"operation_id": f"op-{uuid4()}",
                               "action": "approve", "nonce": nonce}).encode()
        response = await client.post(
            path, data=decision,
            headers=headers(account, purpose="sign_in", body=decision, path=path))
        assert response.status == 200
    assert (await response.json())["state"] == "committed"
    assert (await store.record_for(account=ACCT_B)).person_id == PERSON_A


async def test_refusal_leaves_the_pair_unlinked(surface):
    client, store, _registry, _links, now = surface
    body = json.dumps({"request_id": "generated-request",
                       "receiving": {"platform": "discord", "kind": "user_id",
                                     "value": ACCT_B.value},
                       "surviving_person": PERSON_A}).encode()
    await client.post(
        REQUESTS, data=body,
        headers=headers(ACCT_A, purpose="session", body=body, path=REQUESTS,
                        binding=_binding(PERSON_A, "binding-a")))
    view_path = f"{REQUESTS}/generated-request"
    received = await client.get(
        view_path,
        headers=headers(ACCT_B, purpose="session", body=b"", path=view_path,
                        method="GET", binding=_binding(PERSON_B, "binding-b")))
    nonce = (await received.json())["request"]["challenge"]["nonce"]

    path = f"{REQUESTS}/generated-request/decisions"
    now[0] = datetime.now(UTC) + timedelta(seconds=5)
    decision = json.dumps({"operation_id": f"op-{uuid4()}", "action": "refuse",
                           "nonce": nonce}).encode()
    response = await client.post(
        path, data=decision,
        headers=headers(ACCT_B, purpose="sign_in", body=decision, path=path))
    assert response.status == 200
    assert (await response.json())["state"] == "refused"
    assert (await store.record_for(account=ACCT_B)).person_id == PERSON_B


async def test_holder_exact_unlink_revokes_the_binding(surface):
    client, store, _registry, _links, _now = surface
    body = json.dumps({"operation_id": f"op-{uuid4()}",
                       "expected_person": PERSON_B}).encode()
    response = await client.post(
        UNLINK, data=body,
        headers=headers(ACCT_B, purpose="session", body=body, path=UNLINK,
                        binding=_binding(PERSON_B, "binding-b")))
    assert response.status == 200
    assert await store.record_for(account=ACCT_B) is None
