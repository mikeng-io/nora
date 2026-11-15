"""Actual portal data routes require retained authentication and cannot select another viewer."""

import asyncio
import hashlib
import json
import re
from dataclasses import replace

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.people import AccountSessions
from nora.persons import Persons
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.managed import FakeBindingStore, FakeIdentityRegistry
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
    entered = asyncio.Event()

    @web.middleware
    async def observe_write(request, handler):
        if request.path.endswith("/state"):
            await request.read()
            entered.set()
        return await handler(request)

    app = web.Application(middlewares=[observe_write])
    app["test_write_started"] = entered
    register_web_routes(app, store=FakeRecordStore(), honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(), persons=Persons(by_person={"ken": KEN, "mei": MEI, "jo": JO},
                                                     index={}), ai_peer="nora", started_at=0.0,
        presence=None, presence_circles=(), skills_dir=NO_SKILLS_DIR, events=_fake_events(),
        doctrine_store=build_test_doctrine_store(), documents=world.store, rooms_of=world.rooms_of)
    verifier = AuthenticationVerifier([ISSUER])
    register_session_routes(app, sessions=AccountSessions(store, registry), verifier=verifier)
    register_managed_viewer(app, bindings=store, registry=registry,
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
