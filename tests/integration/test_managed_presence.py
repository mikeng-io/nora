"""Generated provider HTTP and PostgreSQL exercise managed collection ownership."""

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.config import PresenceConfig
from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.infra.life360 import Life360Client, ReadStatus
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.managed_identity import MANAGED_MUTATION_LOCK, SqlBindingStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.schema import PRESENCE_FIXES
from nora.presence.collector import CircleSpec, PresenceCollector
from nora.presence.composition import PresenceStartupError, build_collector
from nora.presence.managed_accounts import ManagedPresenceAccounts
from nora.testing.managed import FakeIdentityRegistry
from tests.integration.managed_ory import ory as _ory

ory = _ory
pytestmark = pytest.mark.asyncio
CIRCLE = "generated-provider-circle"
PERSON = "generated-presence-identity"
MEMBER = "generated-provider-member"
NOW = 1_800_000_000_000


def _row(member=MEMBER, ts=NOW):
    return {"id": member, "features": {"shareLocation": "1"},
            "location": {"timestamp": str(ts // 1000), "latitude": "22.3",
                         "longitude": "114.1", "accuracy": "10"}}


@pytest.fixture
async def case(clean_pg_url):
    run_migrations(clean_pg_url)
    engine = create_sql_engine(clean_pg_url)
    bindings, registry = SqlBindingStore(engine), FakeIdentityRegistry()
    ident = Identifier("life360", "member_id", MEMBER)
    await bindings.register(person=ManagedPerson(PERSON, PersonLifecycle.ACTIVE),
        account=ManagedAccount(str(uuid4()), PERSON, "life360", frozenset({ident}),
                               AccountState.BOUND))
    await registry.ensure_identity(external_id=PERSON, traits={"display_name": "Generated Name"})
    env = SimpleNamespace(engine=engine, bindings=bindings, registry=registry, ident=ident,
        body={"members": [_row(), _row("generated-unknown-member", ts=NOW + 1000)]}, status=200,
        reads=[], pings=[])

    async def members(request):
        env.reads.append(request.match_info["circle"])
        assert request.headers["Authorization"] == "Bearer generated-local-token"
        return web.json_response(env.body, status=env.status)

    async def places(_request):
        return web.json_response({"places": []})

    async def ping(request):
        env.pings.append((request.match_info["circle"], request.match_info["member"]))
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/circles/{circle}/members", members)
    app.router.add_get("/circles/{circle}/places", places)
    app.router.add_post("/circles/{circle}/members/{member}/request", ping)
    async with TestServer(app) as server:
        base = str(server.make_url("/")).rstrip("/")
        env.client = Life360Client(client_credential="", username="", password="",
            access_token="generated-local-token",  # noqa: S106 — generated local HTTP credential
            members_url=base + "/circles/{circle_id}/members",
            places_url=base + "/circles/{circle_id}/places",
            request_url=base + "/circles/{circle_id}/members/{member_id}/request")
        env.store = PresenceStore(engine)
        env.accounts = ManagedPresenceAccounts(bindings, registry)
        env.circle = CircleSpec(CIRCLE, {"generated-unknown-member": "generated-forged-owner"})
        env.collector = PresenceCollector(client=env.client, store=env.store,
            circles=(env.circle,), cadence_seconds=90, managed_accounts=env.accounts)
        try:
            yield env
        finally:
            await engine.dispose()


async def _fixes(case):
    async with case.engine.connect() as conn:
        return (await conn.execute(sa.select(PRESENCE_FIXES))).mappings().all()


async def test_provider_collection_records_only_selected_verified_accounts(case):
    assert (await case.collector.collect_once(case.circle, now_ms=NOW)).status is ReadStatus.OK
    rows = await _fixes(case)
    assert [(r["person_id"], r["circle_id"]) for r in rows] == [(PERSON, CIRCLE)]
    assert case.reads == [CIRCLE]
    assert await case.bindings.resolve("life360", "generated-unknown-member") is None
    assert await case.bindings.memberships_for(PERSON) == ()


@pytest.mark.parametrize("change", ["revoked", "held", "suspended", "remote-inactive", "outage"])
async def test_later_collection_rechecks_binding_hold_and_identity_before_writing(case, change):
    await case.collector.collect_once(case.circle, now_ms=NOW)
    assert len(await _fixes(case)) == 1
    if change == "revoked":
        await case.bindings.revoke_binding(account=case.ident)
    elif change == "held":
        await case.bindings.hold_identity(person_id=PERSON, operation_id="generated-publication")
    elif change == "suspended":
        await case.bindings.set_lifecycle(person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    elif change == "remote-inactive":
        await case.registry.set_state(external_id=PERSON, active=False)
    else:
        case.registry.down = True
    case.body = {"members": [_row(ts=NOW + 1000)]}
    result = await case.collector.collect_once(case.circle, now_ms=NOW + 1000)
    assert result.status is (ReadStatus.UNAVAILABLE if change == "outage" else ReadStatus.OK)
    assert len(await _fixes(case)) == 1


async def test_fresh_sql_store_records_the_canonical_survivor_without_rewriting_old_fixes(case):
    await case.collector.collect_once(case.circle, now_ms=NOW)
    survivor = "generated-presence-survivor"
    await case.bindings.register(person=ManagedPerson(survivor, PersonLifecycle.ACTIVE),
        account=ManagedAccount(str(uuid4()), survivor, "life360",
            frozenset({Identifier("life360", "member_id", "generated-other-member")}),
            AccountState.BOUND))
    await case.registry.ensure_identity(external_id=survivor, traits={})
    await case.bindings.consolidate(survivor=survivor, former=PERSON)
    await case.registry.set_state(external_id=PERSON, active=False)
    fresh = ManagedPresenceAccounts(SqlBindingStore(case.engine), case.registry)
    collector = PresenceCollector(client=case.client, store=case.store,
        circles=(CircleSpec(CIRCLE, {}),), cadence_seconds=90, managed_accounts=fresh)
    case.body = {"members": [_row(ts=NOW + 1000)]}
    await collector.collect_once(CircleSpec(CIRCLE, {}), now_ms=NOW + 1000)
    assert {r["person_id"] for r in await _fixes(case)} == {PERSON, survivor}


@pytest.mark.parametrize("body", [{"members": [None]}, {"members": [{"id": ""}]},
                                    {"members": [_row(), _row()]},
                                    {"members": [{"id": ["generated-bad-id"]}]}])
async def test_malformed_provider_identity_inventory_is_unavailable(case, body):
    case.body = body
    assert (await case.collector.collect_once(case.circle, now_ms=NOW)).status is (
        ReadStatus.UNAVAILABLE)
    assert await _fixes(case) == []
    case.body = {"members": [_row()]}
    assert (await case.collector.collect_once(case.circle, now_ms=NOW)).status is ReadStatus.OK
    assert len(await _fixes(case)) == 1


async def test_revocation_cannot_commit_between_identity_check_and_fix_write(case):
    entered, release = asyncio.Event(), asyncio.Event()

    class ParkedStore(PresenceStore):
        async def record_fix(self, *, fix):
            entered.set()
            await release.wait()
            return await super().record_fix(fix=fix)

    collector = PresenceCollector(client=case.client, store=ParkedStore(case.engine),
        circles=(case.circle,), cadence_seconds=90, managed_accounts=case.accounts)
    write = asyncio.create_task(collector.collect_once(case.circle, now_ms=NOW))
    await asyncio.wait_for(entered.wait(), 5)
    probe = sa.select(sa.func.pg_try_advisory_xact_lock(
        sa.func.hashtextextended(MANAGED_MUTATION_LOCK, 0)))
    try:
        async with case.engine.begin() as independent:
            assert (await independent.execute(probe)).scalar_one() is False
    finally:
        release.set()
        await asyncio.wait_for(write, 10)
    async with case.engine.begin() as independent:
        assert (await independent.execute(probe)).scalar_one() is True
    other = SqlBindingStore(case.engine)
    async with other.mutation():
        await other.revoke_binding(account=case.ident)
    assert len(await _fixes(case)) == 1
    case.body = {"members": [_row(ts=NOW + 1000)]}
    await collector.collect_once(case.circle, now_ms=NOW + 1000)
    assert len(await _fixes(case)) == 1


async def test_refresh_uses_fresh_observation_and_rechecks_current_ownership(case):
    now = int(time.time() * 1000)
    case.body = {"members": [_row(ts=now)]}
    await case.collector.collect_once(case.circle, now_ms=now)
    case.body = {"members": [_row(ts=now + 1000)]}
    assert await case.collector.refresh_member(person_id=PERSON, timeout_seconds=0)
    assert case.pings == [(CIRCLE, MEMBER)]
    await case.bindings.revoke_binding(account=case.ident)
    assert not await case.collector.refresh_member(person_id=PERSON, timeout_seconds=0)
    assert case.pings == [(CIRCLE, MEMBER)]


async def test_failed_or_expired_roster_does_not_authorize_a_provider_refresh(case):
    now = int(time.time() * 1000)
    await case.collector.collect_once(case.circle, now_ms=now - 200_000)
    assert not await case.collector.refresh_member(person_id=PERSON, timeout_seconds=0)
    assert case.pings == [] and len(await _fixes(case)) == 1
    await case.collector.collect_once(case.circle, now_ms=now)
    case.status = 503
    await case.collector.collect_once(case.circle, now_ms=now + 1)
    assert not await case.collector.refresh_member(person_id=PERSON, timeout_seconds=0)
    assert case.pings == []


async def test_managed_factory_requires_explicit_selection_and_never_loads_identity_file(
    case, example_config, tmp_path,
):
    from nora.config_identity import IdentityBackendConfig, ManagedOryConfig

    cfg = example_config.model_copy(update={
        "presence": PresenceConfig(enabled=True, managed_circle_ids=(CIRCLE,)),
        "identity_file": tmp_path / "generated-absent-identity.toml",
        "identity": IdentityBackendConfig(backend="managed", managed=ManagedOryConfig(
            kratos_admin_url="http://127.0.0.1:4434", kratos_public_url="http://127.0.0.1:4433",
            keto_read_url="http://127.0.0.1:4466", keto_write_url="http://127.0.0.1:4467")),
    })
    cfg._credentials = {cfg.store.database_url_env: "unused-owned-engine",
                        cfg.presence.auth_token_env: "generated-local-token"}
    with pytest.raises(PresenceStartupError, match="authority"):
        build_collector(cfg)
    with pytest.raises(PresenceStartupError, match="authority"):
        build_collector(cfg, engine=case.engine)
    deps = build_collector(cfg, engine=case.engine, managed_accounts=case.accounts)
    assert deps.identity_reload is None and not cfg.identity_file.exists()
    assert deps.collector.circle_summary()[0]["circle_id"] == CIRCLE
    empty = cfg.model_copy(update={"presence": cfg.presence.model_copy(
        update={"managed_circle_ids": ()})})
    with pytest.raises(PresenceStartupError, match="explicit circle"):
        build_collector(empty, engine=case.engine, managed_accounts=case.accounts)


async def test_file_collector_uses_only_its_file_mapping(case, example_config, tmp_path):
    from nora.config_identity import IdentityBackendConfig
    from nora.presence.composition import open_collector

    path = tmp_path / "generated-file-identity.toml"
    path.write_text('[persons.generated_file]\nname="Generated File Identity"\n'
        '[[persons.generated_file.life360]]\nmember_id="generated-file-member"\n'
        'circle_id="generated-file-circle"\n')
    cfg = example_config.model_copy(update={"identity_file": path,
        "identity": IdentityBackendConfig(), "presence": PresenceConfig(enabled=True)})
    cfg._credentials = {cfg.store.database_url_env: "unused-owned-engine",
                        cfg.presence.auth_token_env: "generated-local-token"}
    # The assigned engine keeps this proof on its disposable PostgreSQL database.
    deps = build_collector(cfg, engine=case.engine)
    assert deps.identity_reload is not None
    assert deps.collector.circle_summary() == [
        {"circle_id": "generated-file-circle", "members": 1}]
    from nora.domain.presence import WireState
    from nora.infra.life360 import Member

    assert await deps.collector._record(CircleSpec("generated-file-circle",
        {MEMBER: "generated_file"}), Member(MEMBER, WireState.LIVE,
            ts_ms=NOW, lat=22.3, lng=114.1), now_ms=NOW)
    assert [r["person_id"] for r in await _fixes(case)] == ["generated_file"]
    disabled = cfg.model_copy(update={"presence": PresenceConfig()})
    async with open_collector(disabled) as inert:
        assert inert.collector is None and inert.store is None


async def test_managed_daemon_factory_owns_real_ory_and_fresh_sql_without_file_identity(
    case, example_config, clean_pg_url, tmp_path, ory,
):
    from aiohttp import ClientSession

    from nora.config_identity import IdentityBackendConfig, ManagedOryConfig
    from nora.domain.presence import WireState
    from nora.infra.life360 import Member
    from nora.people import managed_services
    from nora.policy import load_policy
    from nora.presence.composition import open_collector

    managed = ManagedOryConfig(kratos_admin_url=ory.kratos_admin,
        kratos_public_url=ory.kratos_public, keto_read_url=ory.keto_read,
        keto_write_url=ory.keto_write)
    policy_path = tmp_path / "generated-presence-policy.toml"
    policy_path.write_text('[[conversations]]\nplatform="signal"\nid="generated-room"\n'
                          'label="Generated Presence Room"\nunit="generated-unit"\npersons="*"\n')
    cfg = example_config.model_copy(update={
        "identity": IdentityBackendConfig(backend="managed", managed=managed),
        "identity_file": tmp_path / "absent-generated-identity.toml",
        "policy_file": policy_path,
        "presence": PresenceConfig(enabled=True, managed_circle_ids=(CIRCLE,)),
    })
    cfg._credentials = {cfg.store.database_url_env: clean_pg_url,
                        cfg.presence.auth_token_env: "generated-local-token"}
    async with ClientSession() as session:
        services = managed_services(managed=managed, session=session,
            policy=load_policy(policy_path), engine=case.engine, credentials={})
        outcome, remote = await services.registry.ensure_identity(external_id=PERSON,
            traits={"display_name": "Generated Presence Identity", "social": True})
        assert remote and outcome.status.value == "complete"
        await case.bindings.correlate_kratos(person_id=PERSON, kratos_identity_id=remote)
        async with open_collector(cfg) as deps:
            assert deps.identity_reload is None and not cfg.identity_file.exists()
            assert await deps.collector._record(CircleSpec(CIRCLE, {}),
                Member(MEMBER, WireState.LIVE, ts_ms=NOW, lat=22.3, lng=114.1), now_ms=NOW)
        await services.registry.set_state(external_id=PERSON, active=False)
        async with open_collector(cfg) as restarted:
            assert not await restarted.collector._record(CircleSpec(CIRCLE, {}),
                Member(MEMBER, WireState.LIVE, ts_ms=NOW + 1000, lat=22.3, lng=114.1),
                now_ms=NOW + 1000)
        assert len(await _fixes(case)) == 1


@pytest.mark.parametrize("circles", [("",), ("generated-circle", "generated-circle"), (" bad ",)])
async def test_managed_collection_config_rejects_ambiguous_selection(circles):
    with pytest.raises(ValueError, match="distinct nonblank"):
        PresenceConfig(managed_circle_ids=circles)


async def test_managed_health_does_not_report_unknown_or_failed_collection_as_empty_success(case):
    from aiohttp.test_utils import TestClient

    from nora.obs.metrics import Metrics
    from nora.presence.__main__ import _metrics_app

    async with TestClient(TestServer(_metrics_app(Metrics(), case.collector))) as client:
        response = await client.get("/health")
        assert response.status == 503
        assert (await response.json())["circles"] == [
            {"circle_id": CIRCLE, "observed_members": None}]
        now = int(time.time() * 1000)
        await case.collector.collect_once(case.circle, now_ms=now)
        response = await client.get("/health")
        assert response.status == 200
        assert (await response.json())["circles"][0]["observed_members"] == 2
        case.status = 503
        await case.collector.collect_once(case.circle, now_ms=now + 1)
        response = await client.get("/health")
        assert response.status == 503
        assert (await response.json())["circles"][0]["observed_members"] is None


@pytest.mark.fresh_database
async def test_managed_sql_write_failure_invalidates_health_without_logging_private_sql(
    case, caplog,
):
    async with case.engine.begin() as conn:
        await conn.exec_driver_sql("""CREATE FUNCTION generated_presence_fault() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'generated-private-sql-detail'; END $$""")
        await conn.exec_driver_sql(f'CREATE TRIGGER generated_presence_fault BEFORE INSERT '
            f'ON "{PRESENCE_FIXES.name}" FOR EACH ROW EXECUTE FUNCTION generated_presence_fault()')
    result = await case.collector.collect_once(case.circle, now_ms=NOW)
    assert result.status is ReadStatus.UNAVAILABLE
    assert await _fixes(case) == []
    assert not case.collector.collection_ready(now_ms=NOW)
    assert "generated-private-sql-detail" not in caplog.text
    async with case.engine.begin() as conn:
        await conn.exec_driver_sql(
            f'DROP TRIGGER generated_presence_fault ON "{PRESENCE_FIXES.name}"')
        await conn.exec_driver_sql("DROP FUNCTION generated_presence_fault()")
    assert (await case.collector.collect_once(case.circle, now_ms=NOW)).status is ReadStatus.OK
    assert len(await _fixes(case)) == 1
