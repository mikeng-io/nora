"""The actual startup helper mounts a scoped private API in managed mode only."""

import pytest
from aiohttp import web

from nora.app.composition_stores import managed_identity
from nora.app.management import mount_management
from nora.app.startup import StartupError
from nora.config import ConfigError, load
from nora.domain.identity import Identifier
from nora.domain.managed import MembershipState, OutcomeStatus, PersonLifecycle, Verdict
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

ROOM = Scope(ScopeKind.ROOM, "discord/generated-guild/generated-channel")
TOKEN = "generated-private-provisioning-token"  # noqa: S105
KEY = "NORA_TEST_MANAGED_PROVISIONING_TOKEN"
PATH = "/app/v1/management/operations"
SETTINGS = '''
[identity]
backend = "managed"
[identity.managed]
kratos_admin_url = "http://127.0.0.1:4434"
keto_read_url = "http://127.0.0.1:4466"
keto_write_url = "http://127.0.0.1:4467"
[identity.provisioning.synthetic_bridge]
token_env = "NORA_TEST_MANAGED_PROVISIONING_TOKEN"
sources = ["discord"]
scopes = [{kind = "room", id = "discord/generated-guild/generated-channel"}]
'''


@pytest.fixture
def configuration(example_config_path, monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    base = example_config_path.read_text()

    def configured(settings=SETTINGS, secret=TOKEN):
        example_config_path.write_text(base + settings)
        private = example_config_path.parent / "secret.toml"
        if secret is not None:
            private.write_text(f'[credentials]\n{KEY} = "{secret}"\n')
        return load(example_config_path, require_secrets=False)

    return configured


def services(*, known=True, bindings=None):
    return ManagedServices(
        bindings if bindings is not None else FakeBindingStore(), FakeAuthorityBackend(),
        FakeTopologySource(
            rooms={ROOM.id: ("generated-unit", False)} if known else {},
            units={"generated-unit": "generated-workspace"},
            workspaces={"generated-workspace"},
        ), FakeIdentityRegistry(), FakeLinkRequests(),
    )


@pytest.fixture
async def sql_services(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    managed = services(bindings=SqlBindingStore(engine=engine))
    try:
        yield managed
    finally:
        await managed.aclose()


def request():
    account = {"platform": "discord", "kind": "user_id", "value": "generated-account"}
    return {
        "operation_id": "generated-registration", "kind": "register_account",
        "target": {"kind": "account", "account": account},
        "payload": {"person_id": "generated-person", "identifiers": [account],
                    "scope": {"kind": "room", "id": ROOM.id}},
        "observed_revisions": {"ownership": "0", "authority": "0"},
    }


async def test_persisted_publication_hold_blocks_resolution_retry_and_permission_reads(
    configuration, aiohttp_client, sql_services, pg_module_url,
):
    managed = sql_services
    app = web.Application()
    await mount_management(app, cfg=configuration(), services=managed)
    client = await aiohttp_client(app)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert (await client.post(PATH, headers=headers, json=request())).status == 200
    await managed.backend.put_relation(scope=ROOM, relation="member", person_id="generated-person")
    assert (await managed.authority.check(scope=ROOM, person_id="generated-person",
                                         permit="view")).verdict is Verdict.ALLOWED
    held = await managed.bindings.hold_identity(
        person_id="generated-person", operation_id="generated-publication",
    )
    assert held.status is OutcomeStatus.COMPLETE
    engine = create_sql_engine(pg_module_url)
    try:
        assert await SqlBindingStore(engine).identity_held("generated-person")
    finally:
        await engine.dispose()
    query = {"account": request()["target"]["account"],
             "scope": {"kind": ROOM.kind.value, "id": ROOM.id}}
    resolution = "/app/v1/management/accounts/resolve"
    unavailable = await client.post(resolution, headers=headers, json=query)
    assert unavailable.status == 503
    assert await unavailable.json() == {"status": "unavailable"}
    assert (await client.post(PATH, headers=headers, json=request())).status == 503
    assert (await managed.authority.check(scope=ROOM, person_id="generated-person",
                                         permit="view")).verdict is Verdict.UNAVAILABLE
    await managed.bindings.release_identity_hold(
        person_id="generated-person", operation_id="generated-other",
    )
    assert (await client.post(resolution, headers=headers, json=query)).status == 503
    await managed.bindings.release_identity_hold(
        person_id="generated-person", operation_id="generated-publication",
    )
    assert (await client.post(resolution, headers=headers, json=query)).status == 200
    assert (await client.post(PATH, headers=headers, json=request())).status == 200


async def test_private_secret_mounts_the_real_registration_service(configuration, aiohttp_client):
    cfg = configuration()
    managed = services()
    app = web.Application()
    await mount_management(app, cfg=cfg, services=managed)
    client = await aiohttp_client(app)
    denied = await client.post(PATH, json=request())
    assert denied.status == 401
    assert await managed.bindings.resolve("discord", "generated-account") is None
    response = await client.post(PATH, json=request(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status == 200
    account = await managed.bindings.resolve("discord", "generated-account")
    assert account is not None and account.person_id == "generated-person"
    assert len(await managed.bindings.memberships_for("generated-person")) == 1


async def test_private_api_registration_and_an_identical_retry_use_the_real_database(
    configuration, aiohttp_client, sql_services,
):
    managed = sql_services
    app = web.Application()
    await mount_management(app, cfg=configuration(), services=managed)
    client = await aiohttp_client(app)
    first = await client.post(PATH, json=request(),
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert first.status == 200
    account = await managed.bindings.resolve("discord", "generated-account")
    assert account is not None and account.person_id == "generated-person"
    retry = await client.post(PATH, json=request(),
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert retry.status == 200
    assert await managed.bindings.accounts_for("generated-person") == (account,)
    await managed.bindings.set_membership(person_id="generated-person", scope=ROOM,
                                          member=False, source="registration")
    retry = await client.post(PATH, json=request(),
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert retry.status == 200
    membership, = await managed.bindings.memberships_for("generated-person")
    assert membership.state is MembershipState.LEFT


@pytest.mark.parametrize("change", ["revoke-binding", "suspend-person"])
async def test_completed_registration_cannot_restore_revoked_or_suspended_identity(
    configuration, aiohttp_client, sql_services, change,
):
    app = web.Application()
    await mount_management(app, cfg=configuration(), services=sql_services)
    client = await aiohttp_client(app)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = await client.post(PATH, json=request(), headers=headers)
    assert response.status == 200
    if change == "revoke-binding":
        await sql_services.bindings.revoke_binding(account=Identifier(
            "discord", "user_id", "generated-account",
        ))
    else:
        await sql_services.bindings.set_lifecycle(
            person_id="generated-person", lifecycle=PersonLifecycle.SUSPENDED,
        )
    response = await client.post(PATH, json=request(), headers=headers)
    assert response.status == 403


async def test_environment_override_replaces_the_private_file_value(
    configuration, monkeypatch, aiohttp_client,
):
    cfg = configuration()
    override = "generated-environment-override"
    monkeypatch.setenv(KEY, override)
    app = web.Application()
    await mount_management(app, cfg=cfg, services=services())
    client = await aiohttp_client(app)
    response = await client.post(PATH, json=request(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status == 401
    response = await client.post(
        PATH, json=request(), headers={"Authorization": f"Bearer {override}"},
    )
    assert response.status == 200


async def test_file_mode_has_no_management_write_endpoint_or_secret_requirement(
    configuration, aiohttp_client,
):
    cfg = configuration(SETTINGS.replace('backend = "managed"', 'backend = "file"'), secret=None)
    app = web.Application()
    await mount_management(app, cfg=cfg, services=None)
    client = await aiohttp_client(app)
    response = await client.post(PATH, json=request(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status == 404


@pytest.mark.parametrize("secret", [None, "", " whitespace "])
async def test_missing_or_invalid_credential_refuses_startup(configuration, secret):
    app = web.Application()
    with pytest.raises(StartupError, match="credential"):
        await mount_management(app, cfg=configuration(secret=secret), services=services())
    assert list(app.router.routes()) == []


async def test_an_unknown_resource_scope_refuses_startup(configuration):
    with pytest.raises(StartupError, match="topology"):
        await mount_management(
            web.Application(), cfg=configuration(), services=services(known=False),
        )


async def test_two_service_identities_cannot_share_a_credential(configuration):
    duplicate = SETTINGS + '''
[identity.provisioning.another_bridge]
token_env = "NORA_TEST_MANAGED_PROVISIONING_TOKEN"
sources = ["discord"]
scopes = [{kind = "room", id = "discord/generated-guild/generated-channel"}]
'''
    with pytest.raises(StartupError, match="ambiguous"):
        await mount_management(web.Application(), cfg=configuration(duplicate), services=services())


async def test_a_declared_ory_secret_cannot_silently_be_omitted(configuration, monkeypatch):
    monkeypatch.delenv("NORA_TEST_KETO_ADMIN_KEY", raising=False)
    settings = SETTINGS.replace(
        "[identity.managed]", '[identity.managed]\nketo_key_env = "NORA_TEST_KETO_ADMIN_KEY"',
    )
    with pytest.raises(StartupError, match="credentials"):
        await managed_identity(configuration(settings), None, None, None)


async def test_ory_boot_probes_receive_resolved_private_credentials(
    configuration, example_config_path, monkeypatch, aiohttp_server, pg_module_url,
):
    monkeypatch.delenv("NORA_TEST_KETO_ADMIN_KEY", raising=False)
    monkeypatch.delenv("NORA_TEST_KRATOS_ADMIN_KEY", raising=False)
    calls = []

    async def kratos(request):
        if request.headers.get("Authorization") != "Bearer generated-kratos-key":
            return web.json_response({"error": "unauthorized"}, status=401)
        calls.append("kratos")
        return web.json_response({"error": "absent"}, status=404)

    async def keto(request):
        if request.headers.get("Authorization") != "Bearer generated-keto-key":
            return web.json_response({"error": "unauthorized"}, status=401)
        calls.append("keto")
        return web.json_response({"relation_tuples": [], "next_page_token": ""})

    app = web.Application()
    app.router.add_get("/admin/identities/by/external/boot-probe", kratos)
    app.router.add_get("/relation-tuples", keto)
    server = await aiohttp_server(app)
    url = str(server.make_url("")).rstrip("/")
    settings = SETTINGS.replace("http://127.0.0.1:4434", url).replace(
        "http://127.0.0.1:4466", url,
    ).replace("http://127.0.0.1:4467", url).replace(
        "[identity.managed]", '[identity.managed]\n'
        'keto_key_env = "NORA_TEST_KETO_ADMIN_KEY"\n'
        'kratos_admin_key_env = "NORA_TEST_KRATOS_ADMIN_KEY"',
    )
    cfg = configuration(settings)
    private = example_config_path.parent / "secret.toml"
    private.write_text(private.read_text() + '\n'
                       'NORA_TEST_KETO_ADMIN_KEY = "generated-keto-key"\n'
                       'NORA_TEST_KRATOS_ADMIN_KEY = "generated-kratos-key"\n')
    engine = create_sql_engine(pg_module_url)
    wiring = None
    try:
        wiring = await managed_identity(cfg, None, None, engine)
        assert calls == ["kratos", "keto"]
    finally:
        if wiring is not None:
            await wiring.services.aclose()
            await wiring.session.close()
        await engine.dispose()


@pytest.mark.parametrize("old,new", [
    ('sources = ["discord"]', 'sources = ["unknown-platform"]'),
    ('sources = ["discord"]', 'sources = []'),
    ('kind = "room"', 'kind = "server"'),
    ('id = "discord/generated-guild/generated-channel"', 'id = " "'),
    ('kind = "room"', 'kind = "room", actor = "forged"'),
    ('token_env = "NORA_TEST_MANAGED_PROVISIONING_TOKEN"', 'token_env = "literal secret"'),
])
def test_malformed_source_scope_or_secret_reference_is_not_a_valid_configuration(
    configuration, old, new,
):
    with pytest.raises(ConfigError):
        configuration(SETTINGS.replace(old, new))
