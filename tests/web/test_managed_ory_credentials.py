"""Ory admin credentials stay on the private listener calls alone —
the real `managed_services` composition's positive control — and never
reach a result the browser or a model-facing surface could serialize
(`authorization.md`: every URL names a private listener; credentials
never appear inside a URL or a reply)."""

import hashlib
import time
from types import SimpleNamespace

from aiohttp import web

from nora.config_identity import ManagedOryConfig
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.obs.metrics import Metrics
from nora.people import AccountSessions
from nora.people.app.managed import ManagedServices
from nora.people.adapters.tools import _make_member_update
from nora.people.wire import managed_services
from nora.policy import load_policy
from nora.testing.managed import FakeBindingStore
from nora.web.management_api import ProvisioningCredential, register_management_routes
from nora.web.management_auth import AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from tests.unit.test_managed_admission import POLICY
from tests.unit.test_management_authentication import ISSUER, claims, signed

KETO_KEY = "generated-keto-admin-4c1d9b"
KRATOS_KEY = "generated-kratos-admin-77e2af"
PROVISION = "generated-provisioning-credential"  # noqa: S105
SENTINELS = (KETO_KEY, KRATOS_KEY)
ROOM = Scope(ScopeKind.ROOM, "signal/generated-room-a")
CHECK = "/app/v1/management/auth/check"


class _Response:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self._body = body

    async def read(self):
        return self._body


class _Call:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _OrySession:
    """The aiohttp-session stand-in `managed_services` injects into both
    Ory clients: it records every request's headers, then answers or
    fails as scripted — never a live service."""

    def __init__(self, *, status=200, body=b"{}", error=None):
        self.seen = []
        self._response = _Response(status, body)
        self._error = error

    def request(self, method, url, **kwargs):
        self.seen.append((method, str(url), dict(kwargs.get("headers") or {})))
        if self._error is not None:
            raise self._error
        status = 201 if method.upper() == "PUT" else self._response.status
        return _Call(_Response(status, self._response._body))


def _managed(session, tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(POLICY)
    return managed_services(
        managed=ManagedOryConfig(
            kratos_admin_url="http://127.0.0.1:4434",
            kratos_public_url="http://127.0.0.1:4433",
            keto_read_url="http://127.0.0.1:4466",
            keto_write_url="http://127.0.0.1:4467",
            kratos_admin_key_env="KRATOS_ADMIN_KEY",
            keto_key_env="KETO_ADMIN_KEY",
        ),
        session=session,
        policy=load_policy(path),
        engine=SimpleNamespace(),
        credentials={
            "KRATOS_ADMIN_KEY": KRATOS_KEY,
            "KETO_ADMIN_KEY": KETO_KEY,
        },
    )


async def _surface(aiohttp_client, session, tmp_path):
    """The composed services over a scripted Ory transport, mounted on the
    real private routes the management API and the portal session check
    expose."""
    wired = _managed(session, tmp_path)
    # Same real Ory adapters the composition built, re-assembled over the
    # fake store every web test uses.
    services = ManagedServices(
        bindings=(store := FakeBindingStore()),
        backend=wired.backend, topology=wired.topology,
        registry=wired.registry, links=wired.links,
        freshness=wired.freshness,
    )
    account = Identifier(platform="discord", kind="user_id", value="generated-account")
    await store.register(
        person=ManagedPerson("generated-person", PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            "generated-account", "generated-person", "discord",
            frozenset({account}), AccountState.BOUND),
    )
    app = web.Application()
    register_management_routes(
        app, services=services,
        credentials=(
            ProvisioningCredential(
                actor=Actor(ActorKind.SERVICE, service_id="generated-bridge",
                            sources={"signal", "discord"}),
                token=PROVISION, scopes={ROOM}),
        ),
    )
    register_session_routes(
        app, sessions=AccountSessions(store, services.registry),
        verifier=AuthenticationVerifier([ISSUER]),
    )
    return await aiohttp_client(app), services, store


async def test_ory_admin_keys_ride_only_their_private_calls(tmp_path):
    """Positive control: each admin key reaches its own Ory listener as a
    private `Authorization` header — and nothing carries it in a URL."""
    session = _OrySession(body=b'{"relation_tuples": []}')
    services = _managed(session, tmp_path)
    assert (await services.backend.put_relation(
        scope=ROOM, relation="member", person_id="generated-person")
    ).status is OutcomeStatus.COMPLETE
    await services.registry.identity_state(external_id="generated-person")
    assert len(session.seen) == 2
    for _method, url, headers in session.seen:
        expected = KRATOS_KEY if ":4434" in url else KETO_KEY
        assert headers.get("Authorization") == f"Bearer {expected}"
        for sentinel in SENTINELS:
            assert sentinel not in url


async def test_ory_admin_keys_never_reach_an_outcome(tmp_path):
    """Normal and failure paths alike: no `CheckResult`/`Outcome` — the
    values a reply or a model-facing diagnostic serializes — carries an
    admin key."""
    for session in (
        _OrySession(body=b'{"relation_tuples": []}'),
        _OrySession(error=OSError("generated-outage")),
    ):
        services = _managed(session, tmp_path)
        results = [
            await services.backend.scope_relations_on(scope=ROOM),
            await services.backend.put_relation(
                scope=ROOM, relation="member", person_id="generated-person"),
            await services.registry.identity_state(external_id="generated-person"),
        ]
        for result in results:
            for sentinel in SENTINELS:
                assert sentinel not in repr(result)


async def test_ory_admin_keys_never_reach_a_reply_body(tmp_path, aiohttp_client):
    """Every HTTP reply the boundary emits carries only its declared
    fields — on the operations route the `detail` line echoes the
    outcome's own detail, which is a fixed outage label, never request
    material; on resolve and the portal session check the body is a
    status alone."""
    client, _, _ = await _surface(
        aiohttp_client, _OrySession(error=OSError("generated-outage")), tmp_path)
    sync = {
        "operation_id": "generated-sync", "kind": "sync_membership",
        "target": {"kind": "scope",
                   "scope": {"kind": ROOM.kind.value, "id": ROOM.id}},
        "payload": {"members": [
            {"platform": "signal", "kind": "uuid", "value": "generated-uuid"}],
            "complete": False, "observed_ms": int(time.time() * 1000)},
        "observed_revisions": {"authority": "r-1"},
    }
    # The write path surfaces the outcome's detail — pinned to its fixed
    # outage label so the echo path stays honest.
    response = await client.post(
        "/app/v1/management/operations",
        headers={"Authorization": f"Bearer {PROVISION}"}, json=sync)
    body = await response.json()
    assert response.status == 503
    assert body["detail"] == "transport:OSError"
    # Resolve and the session check emit a status shape alone.
    resolve = await client.post(
        "/app/v1/management/accounts/resolve",
        headers={"Authorization": f"Bearer {PROVISION}"},
        json={
            "account": {"platform": "discord", "kind": "user_id",
                        "value": "generated-account"},
            "scope": {"kind": ROOM.kind.value, "id": ROOM.id},
        })
    assert resolve.status == 503
    assert await resolve.json() == {"status": "unavailable"}
    session = await client.post(
        CHECK, data=b"{}",
        headers={"Authorization": "Nora-Authentication " + signed(
            claims(path=CHECK, purpose="session",
                   binding={"person_id": "generated-person",
                            "person_revision": "1",
                            "account_id": "generated-account",
                            "account_revision": "1"}))})
    assert session.status == 503
    assert await session.json() == {"status": "unavailable"}
    for response in (response, resolve, session):
        text = await response.text()
        headers = str(response.headers)
        for sentinel in (*SENTINELS, PROVISION):
            assert sentinel not in text
            assert sentinel not in headers


async def test_ory_admin_keys_never_reach_a_tool_result(tmp_path):
    """The model-facing surface serializes an `Outcome` the same way —
    the member tool's result dict carries the fixed outage label, never
    the credential the failed call was authenticated with."""
    services = _managed(_OrySession(error=OSError("generated-outage")), tmp_path)
    outcome = await services.backend.put_relation(
        scope=ROOM, relation="member", person_id="generated-person")
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "transport:OSError"
    deps = SimpleNamespace(
        metrics=Metrics(),
        room_management=SimpleNamespace(
            invoke=lambda *_args, **_kw: outcome,
            render=lambda _result: "generated-render"),
        message=SimpleNamespace(platform="signal"),
    )
    async def invoke(*_args, **_kw):
        return outcome
    deps.room_management.invoke = invoke
    result = await _make_member_update(deps)(action="add")
    assert result["status"] == "unavailable"
    assert "transport:OSError" in result["note"]
    for sentinel in (*SENTINELS, PROVISION):
        assert sentinel not in str(result)
