"""Actual portal data routes require retained authentication and cannot select another viewer."""

import asyncio
import hashlib
import json
import re
from dataclasses import replace

import pytest
from aiohttp import web

from nora.agent.whereis import Circle
from nora.domain.culture import character_key
from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.domain.management import Scope, ScopeKind
from nora.people import AccountSessions, PortalScopes
from nora.people.app.managed_authority import AuthorityService
from nora.persons import Persons
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeTopologySource,
)
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from nora.web.managed_viewer import register_managed_viewer
from nora.web.management_auth import AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from tests.conftest import build_test_doctrine_store
from tests.unit.test_management_authentication import ISSUER, claims, signed
from tests.web.test_api import NO_SKILLS_DIR, _fake_events
from tests.web.test_documents_api import JO, KEN, MEI, _seed, _World
from tests.web.test_management_sessions import sign_in
from tests.web.test_memory import _line, _Profile


def proof(value, path, body=b"", method="GET"):
    return {"Authorization": "Nora-Authentication " + signed({**value, "path": path,
        "method": method, "body_sha256": hashlib.sha256(body).hexdigest()})}


@pytest.fixture
async def surface(aiohttp_client, request):
    world = _World()
    await _seed(world)
    store, registry = FakeBindingStore(), FakeIdentityRegistry()
    await store.register(person=ManagedPerson("ken", PersonLifecycle.ACTIVE),
        account=ManagedAccount("generated-binding", "ken", "discord",
            frozenset({Identifier("discord", "user_id", "generated-account")}), AccountState.BOUND))
    await registry.ensure_identity(external_id="ken", traits={})
    topology = FakeTopologySource(
        rooms={"signal/r1": ("generated-unit-a", False),
               "signal/r3": ("generated-unit-b", False)},
        units={"generated-unit-a": "generated-workspace-a",
               "generated-unit-b": "generated-workspace-b"},
        workspaces={"generated-workspace-a", "generated-workspace-b"})
    backend = FakeAuthorityBackend()
    for pid, room in (("ken", "r1"), ("mei", "r1"), ("jo", "r3")):
        if pid != "ken":
            await store.register(person=ManagedPerson(pid, PersonLifecycle.ACTIVE),
                account=ManagedAccount("binding-" + pid, pid, "discord", frozenset({
                    Identifier("discord", "user_id", "generated-" + pid)}), AccountState.BOUND))
        scope = Scope(ScopeKind.ROOM, "signal/" + room)
        await store.set_membership(person_id=pid, scope=scope, member=True, source="generated")
        await backend.put_relation(scope=scope, relation="member", person_id=pid)
    authority = AuthorityService(store, backend, topology)
    entered = asyncio.Event()

    @web.middleware
    async def observe_write(request, handler):
        if request.path.endswith("/state"):
            await request.read()
            entered.set()
        return await handler(request)

    app = web.Application(middlewares=[observe_write])
    app["test_write_started"] = entered
    app["test_authority"] = backend
    register_web_routes(app, store=FakeRecordStore(), honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(), persons=Persons(by_person={"ken": KEN, "mei": MEI, "jo": JO},
                                                     index={}), ai_peer="nora", started_at=0.0,
        presence=None, presence_circles=(Circle("generated-circle", frozenset({"ken", "jo"})),),
        skills_dir=NO_SKILLS_DIR, events=_fake_events(),
        doctrine_store=build_test_doctrine_store(), documents=world.store, rooms_of=world.rooms_of,
        profile=_Profile(heads={character_key(unit, "ken"): (
            _line("l-" + unit, "cycles to work", unit=unit,
                  profile_key=character_key(unit, "ken")),)
            for unit in ("generated-unit-a", "generated-unit-b")}))
    verifier = AuthenticationVerifier([ISSUER])
    register_session_routes(app, sessions=AccountSessions(store, registry), verifier=verifier)
    register_managed_viewer(app, bindings=store, registry=registry,
                            scopes=PortalScopes(store, topology, authority),
                            verifier=verifier if getattr(request, "param", True) else None)
    client = await aiohttp_client(app)
    return client, store, registry, world, await sign_in(client)


async def test_real_data_routes_require_current_session_and_exact_query_viewer(surface):
    client, _, registry, _, authentication = surface
    path = "/app/v1/documents?personId=ken"
    assert (await client.get(path)).status == 401
    service = await client.get(path, headers={"Authorization": "Bearer generated-service"})
    assert service.status == 401
    valid = await client.get(path, headers=proof(authentication, path))
    assert valid.status == 200
    assert {row["title"] for row in (await valid.json())["documents"]} == {
        "Lisbon", "Open room note"}
    assert valid.headers["Cache-Control"] == "no-store"
    for other in ("mei", "ken&personId=mei"):
        changed = "/app/v1/documents?personId=" + other
        assert (await client.get(changed, headers=proof(authentication, changed))).status == 403
    assert (await client.get(path, headers=proof(claims(), path))).status == 401
    registry.down = True
    assert (await client.get(path, headers=proof(authentication, path))).status == 503


async def test_backend_binds_document_write_to_body_and_serializes_identity_validation(surface):
    client, store, _, world, authentication = surface
    path = f"/app/v1/documents/{world.ids['a']}/state?personId=ken"
    body = json.dumps({"state": "archived"}).encode()
    headers = {**proof(authentication, path, body, "POST"), "Content-Type": "application/json"}
    altered = json.dumps({"state": "deleted"}).encode()
    assert (await client.post(path, data=altered, headers=headers)).status == 401
    assert (await client.post(path, data=body, headers=headers)).status == 200
    assert (await world.store.get(document_id=world.ids["a"])).summary.state == "archived"
    entered = client.server.app["test_write_started"]
    entered.clear()
    resumed = json.dumps({"state": "active"}).encode()
    resumed_headers = {**proof(authentication, path, resumed, "POST"),
                       "Content-Type": "application/json"}
    async with store.mutation():
        pending = asyncio.create_task(client.post(path, data=resumed, headers=resumed_headers))
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert (await world.store.get(document_id=world.ids["a"])).summary.state == "archived"
        account = store.accounts["generated-binding"]
        store.accounts[account.account_id] = replace(account, revision=account.revision + 1)
    assert (await asyncio.wait_for(pending, timeout=3)).status == 403


async def test_all_registered_portal_data_and_write_routes_are_guarded(surface):
    client, _, _, _, _ = surface
    tested = 0
    for route in client.server.app.router.routes():
        canonical = route.resource.canonical
        if not canonical.startswith("/app/v1/") or "/management/" in canonical:
            continue
        path = re.sub(r"\{[^}]+\}", "generated", canonical)
        response = await client.request(route.method, path)
        assert response.status == 401, (route.method, canonical, response.status)
        tested += 1
    assert tested >= 25


@pytest.mark.parametrize("surface", [False], indirect=True)
async def test_managed_data_routes_fail_closed_without_a_configured_authentication_gateway(surface):
    client, _, _, _, authentication = surface
    path = "/app/v1/persons"
    assert (await client.get(path, headers=proof(authentication, path))).status == 503


async def test_managed_roster_and_documents_follow_current_unit_access_not_the_file_roster(surface):
    client, _, _, world, authentication = surface
    path = "/app/v1/persons"
    people = await (await client.get(path, headers=proof(authentication, path))).json()
    assert {person["personId"] for person in people} == {"ken", "mei"}
    profile = "/app/v1/persons/jo"
    assert (await client.get(profile, headers=proof(authentication, profile))).status == 404
    document = f"/app/v1/documents/{world.ids['c']}?personId=ken"
    assert (await client.get(document, headers=proof(authentication, document))).status == 404
    backend = client.server.app["test_authority"]
    scope = Scope(ScopeKind.ROOM, "signal/r3")
    await backend.put_relation(scope=scope, relation="grant_view", person_id="ken")
    assert (await client.get(document, headers=proof(authentication, document))).status == 200
    assert (await client.get(profile, headers=proof(authentication, profile))).status == 200
    await backend.drop_relation(scope=scope, relation="grant_view", person_id="ken")
    assert (await client.get(document, headers=proof(authentication, document))).status == 404
    backend.down = True
    assert (await client.get(document, headers=proof(authentication, document))).status == 503


async def test_memory_accounts_follow_authorized_units(surface):
    """The store holds ken an account in each of two units; the portal shows
    only the units whose rooms he can currently see."""
    client, _, _, _, authentication = surface
    path = "/app/v1/memory?personId=ken"
    body = await (await client.get(path, headers=proof(authentication, path))).json()
    assert [account["unit"] for account in body] == ["generated-unit-a"]
    backend = client.server.app["test_authority"]
    scope = Scope(ScopeKind.ROOM, "signal/r3")
    await backend.put_relation(scope=scope, relation="grant_view", person_id="ken")
    body = await (await client.get(path, headers=proof(authentication, path))).json()
    assert [account["unit"] for account in body] == ["generated-unit-a", "generated-unit-b"]
    await backend.drop_relation(scope=scope, relation="grant_view", person_id="ken")
    body = await (await client.get(path, headers=proof(authentication, path))).json()
    assert [account["unit"] for account in body] == ["generated-unit-a"]


async def test_visible_profile_aliases_resolve_without_exposing_other_units(surface):
    client, store, _, _, authentication = surface
    await store.add_alias(former_id="generated-former-ken", person_id="ken")
    path = "/app/v1/persons/generated-former-ken"
    response = await client.get(path, headers=proof(authentication, path))
    assert response.status == 200
    assert (await response.json())["personId"] == "ken"
    await store.add_alias(former_id="generated-former-jo", person_id="jo")
    path = "/app/v1/persons/generated-former-jo"
    assert (await client.get(path, headers=proof(authentication, path))).status == 404


async def test_a_shared_location_circle_with_an_unknown_member_is_unavailable_not_smaller(surface):
    client, store, _, _, authentication = surface
    path = "/app/v1/presence?personId=ken"
    assert (await client.get(path, headers=proof(authentication, path))).status == 200
    store.people.pop("jo")
    assert (await client.get(path, headers=proof(authentication, path))).status == 503
