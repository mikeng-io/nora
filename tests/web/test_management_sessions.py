"""The real private routes retain exact bindings and refuse stale or invented principals."""

import hashlib
from dataclasses import replace

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.people import AccountSessions
from nora.testing.managed import FakeBindingStore, FakeIdentityRegistry
from nora.web.management_auth import AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from tests.unit.test_management_authentication import BODY, ISSUER, PATH, claims, signed

CHECK = "/app/v1/management/auth/check"
ACCOUNT = Identifier("discord", "user_id", "generated-account")


@pytest.fixture
async def surface(aiohttp_client):
    store = FakeBindingStore()
    registry = FakeIdentityRegistry()
    await store.register(person=ManagedPerson("generated-person", PersonLifecycle.ACTIVE),
        account=ManagedAccount("generated-binding", "generated-person", "discord",
                               frozenset({ACCOUNT}), AccountState.BOUND))
    await registry.ensure_identity(external_id="generated-person", traits={})
    app = web.Application()
    register_session_routes(app, sessions=AccountSessions(store, registry),
                             verifier=AuthenticationVerifier([ISSUER]))
    return await aiohttp_client(app), store, registry


async def post(client, path=PATH, *, value=None, body=BODY, header=None):
    value = claims() if value is None else value
    value.update(path=path, body_sha256=hashlib.sha256(body).hexdigest())
    authorization = header or "Nora-Authentication " + signed(value)
    return await client.post(path, data=body, headers={"Authorization": authorization})


async def sign_in(client):
    value = claims()
    response = await post(client, value=value)
    assert response.status == 200
    value.update(binding=(await response.json())["binding"], purpose="session")
    return value


async def test_exact_account_session_checks_use_the_captured_current_binding(surface):
    client, _, _ = surface
    value = await sign_in(client)
    response = await post(client, CHECK, value=value)
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert (await response.json())["binding"]["person_id"] == "generated-person"


@pytest.mark.parametrize("failure,expected", [
    ("account-revision", 403), ("person-revision", 403), ("revoked", 403),
    ("suspended", 403), ("kratos-inactive", 403), ("kratos-missing", 503),
    ("registry-down", 503), ("bindings-down", 503), ("publication", 503),
])
async def test_retained_sessions_revalidate_live_facts(surface, failure, expected):
    client, store, registry = surface
    value = await sign_in(client)
    account = store.accounts["generated-binding"]
    person = store.people["generated-person"]
    if failure == "account-revision":
        store.accounts[account.account_id] = replace(account, revision=account.revision + 1)
    elif failure == "person-revision":
        store.people[person.person_id] = replace(person, revision=person.revision + 1)
    elif failure == "revoked":
        store.accounts[account.account_id] = replace(account, state=AccountState.REVOKED)
    elif failure == "suspended":
        await store.set_lifecycle(person_id=person.person_id, lifecycle=PersonLifecycle.SUSPENDED)
    elif failure == "kratos-inactive":
        await registry.set_state(external_id=person.person_id, active=False)
    elif failure == "kratos-missing":
        registry.identities.clear()
    elif failure == "publication":
        await store.hold_identity(person_id=person.person_id, operation_id="generated-publication")
    else:
        (registry if failure == "registry-down" else store).down = True
    response = await post(client, CHECK, value=value)
    assert response.status == expected
    assert "binding" not in await response.json()


async def test_replayed_sign_in_cannot_refresh_the_original_account_revision(surface):
    client, store, _ = surface
    value = claims()
    assert (await post(client, value=value)).status == 200
    account = store.accounts["generated-binding"]
    store.accounts[account.account_id] = replace(account, revision=account.revision + 1)
    assert (await post(client, value=value)).status == 403
    assert (await post(client, value=claims())).status == 200


async def test_service_bearer_and_changed_request_never_authenticate_a_holder(surface):
    client, _, _ = surface
    assert (await post(client, header="Bearer generated-provisioning-token")).status == 401
    token = signed(claims())
    response = await client.post(PATH, data=b'{"actor":"admin"}',
        headers={"Authorization": "Nora-Authentication " + token})
    assert response.status == 401
