"""Ory admin credentials stay on the private listener calls alone —
the real `managed_services` composition's positive control — and never
reach a result the browser or a model-facing surface could serialize
(`authorization.md`: every URL names a private listener; credentials
never appear inside a URL or a reply)."""

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
from nora.people.wire import managed_services
from nora.policy import load_policy
from nora.testing.managed import FakeBindingStore
from nora.web.management_api import ProvisioningCredential, register_management_routes
from tests.unit.test_managed_admission import POLICY

KETO_KEY = "generated-keto-admin-4c1d9b"
KRATOS_KEY = "generated-kratos-admin-77e2af"
PROVISION = "generated-provisioning-credential"
SENTINELS = (KETO_KEY, KRATOS_KEY)
ROOM = Scope(ScopeKind.ROOM, "signal/generated-room-a")


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
    strings a reply or a model-facing diagnostic serializes — carries an
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
    """The real browser boundary: `resolve` reaches Kratos through the
    same composed registry — an Ory outage's 503 body carries neither
    admin key nor the provisioning bearer."""
    session = _OrySession(error=OSError("generated-outage"))
    services = _managed(session, tmp_path)
    services.bindings = store = FakeBindingStore()
    account = Identifier(platform="signal", kind="uuid", value="generated-uuid")
    await store.register(
        person=ManagedPerson("generated-person", PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            "generated-account", "generated-person", "signal",
            frozenset({account}), AccountState.BOUND),
    )
    app = web.Application()
    register_management_routes(
        app, services=services,
        credentials=(
            ProvisioningCredential(
                actor=Actor(ActorKind.SERVICE, service_id="generated-bridge",
                            sources={"signal"}),
                token=PROVISION, scopes={ROOM}),
        ),
    )
    client = await aiohttp_client(app)
    response = await client.post(
        "/app/v1/management/accounts/resolve",
        headers={"Authorization": f"Bearer {PROVISION}"},
        json={
            "account": {"platform": account.platform, "kind": account.kind,
                        "value": account.value},
            "scope": {"kind": ROOM.kind.value, "id": ROOM.id},
        },
    )
    assert response.status == 503
    body = await response.text()
    for sentinel in (*SENTINELS, PROVISION):
        assert sentinel not in body
