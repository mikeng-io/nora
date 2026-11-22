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
from nora.people import ManagedPresenceAccounts
from nora.presence.collector import CircleSpec, PresenceCollector
from nora.presence.composition import PresenceStartupError, build_collector
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
    await bindings.register(person=ManagedPerson(PERSON, PersonLifecycle.ACTIVE,
        display_name="Generated Global Presence"),
        account=ManagedAccount(str(uuid4()), PERSON, "life360", frozenset({ident}),
                               AccountState.BOUND))
    await registry.ensure_identity(external_id=PERSON,
        traits={"display_name": "Generated Global Presence"})
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


@pytest.fixture
async def location(case):
    from aiohttp.test_utils import TestServer

    from nora.agent.room import Room
    from nora.agent.tool_deps import ToolDeps
    from nora.domain.managed import ManagedMembership, MembershipState
    from nora.domain.management import Scope, ScopeKind
    from nora.infra.presence_circles import HttpCircleObservations
    from nora.location.adapters.selected_presence import SelectedPresenceStore
    from nora.location.adapters.tools import build
    from nora.obs.metrics import Metrics
    from nora.people.app.managed import ManagedServices
    from nora.presence.__main__ import _metrics_app
    from nora.testing import FakeRosterSource
    from nora.testing.managed import FakeAuthorityBackend, FakeLinkRequests, FakeTopologySource

    room_scope = Scope(ScopeKind.ROOM, "signal/generated-location-room")
    phone = "+85290000140"
    await case.bindings.register(person=ManagedPerson(PERSON, PersonLifecycle.ACTIVE,
        display_name="Generated Global Presence"), account=ManagedAccount(str(uuid4()),
        PERSON, "signal", frozenset({Identifier("signal", "phone", phone)}), AccountState.BOUND),
        membership=ManagedMembership(PERSON, room_scope, MembershipState.MEMBER, "generated-test"))
    topology = FakeTopologySource(rooms={room_scope.id: ("generated-location-unit", False)},
        units={"generated-location-unit": "generated-workspace"},
        workspaces={"generated-workspace"})
    now = int(time.time() * 1000)
    case.body = {"members": [_row(ts=now)]}
    await case.collector.collect_once(case.circle, now_ms=now)
    key = "generated-directory-key"
    async with TestServer(_metrics_app(Metrics(), case.collector, directory_key=key)) as server:
        observations = HttpCircleObservations(base_url=str(server.make_url("/")), key=key,
                                               circles=(CIRCLE,), ttl_ms=180_000)
        services = ManagedServices(case.bindings, FakeAuthorityBackend(), topology,
            case.registry, FakeLinkRequests(), circle_observations=observations)
        access = services.access
        room = Room(platform="signal", conversation_id="generated-location-room", access=access,
            roster_source=FakeRosterSource(
                groups={"generated-location-room": (frozenset({phone}),)}))
        store = SelectedPresenceStore(case.engine, access=access)
        deps = ToolDeps(store=None, conversation_id="generated-location-room",
            conversation_type="group",
            persons=access.persons_view(), deliverer=None, metrics=Metrics(),
            room=room, transit_source=None, presence_source=store, presence_circles=(),
            content_checks=True, location_trusted=True)
        yield SimpleNamespace(access=access, deps=deps, room=room, server=server,
            tools={tool.name: tool.call for tool in build(deps)},
            store=store, room_scope=room_scope, services=services, observations=observations)


async def test_graph_places_fixes_by_membership_without_a_view_grant(case, location):
    from nora.domain.managed import Verdict
    from nora.domain.management import Scope, ScopeKind
    from nora.domain.presence import PresenceFix, WireState
    from nora.infra.sql.item_registration import (
        RegistrationScope,
        RuntimeRevisions,
        SqlItemRegistration,
    )
    from nora.policy import Policy

    unit = "generated-location-unit"
    access = location.access
    view = await access.check(scope=Scope(ScopeKind.UNIT, unit), person_id=PERSON, permit="view")
    assert view.verdict is Verdict.DENIED
    nonmember = "generated-presence-nonmember"
    identifier = Identifier("life360", "member_id", "generated-nonmember-account")
    await case.bindings.register(person=ManagedPerson(nonmember, PersonLifecycle.ACTIVE),
        account=ManagedAccount(str(uuid4()), nonmember, "life360", frozenset({identifier}),
                               AccountState.BOUND))
    assert await access.canonical_person(nonmember) == nonmember

    policy = Policy(())
    registration = SqlItemRegistration(
        RegistrationScope(policy, None, access=access), RuntimeRevisions({}, policy, None),
    )
    store = PresenceStore(case.engine, registration=registration)
    ts = int(time.time() * 1000) + 1000
    for person_id in (PERSON, nonmember):
        assert await store.record_fix(fix=PresenceFix(
            circle_id=CIRCLE, person_id=person_id, ts=ts, lat=22.3, lng=114.1,
            wire_state=WireState.LIVE, recorded_at=ts,
        ))
    by_person = {r["person_id"]: (r["unit"], r["graph_id"])
                 for r in await _fixes(case) if r["ts"] == ts}
    assert by_person[PERSON][0] == unit and by_person[PERSON][1] is not None
    assert by_person[nonmember] == (None, None)


async def test_real_location_tool_reads_current_managed_circle_and_preserves_provider_boundary(
    case, location,
):
    reads = len(case.reads)
    out = await location.tools["where_is"]("Generated Global Presence")
    assert out["status"] == "ok", out
    assert out["lat"] == 22.3 and out["lng"] == 114.1
    assert len(case.reads) == reads
    await case.bindings.revoke_binding(account=case.ident)
    refused = await location.tools["where_is"]("Generated Global Presence")
    assert refused["status"] == "unavailable"
    assert "lat" not in refused


async def test_location_tool_uses_only_the_trusted_room_nickname(case, location):
    from types import MappingProxyType

    from nora.domain.managed import OperationJournalState, OperationRecord
    from nora.domain.management import Scope, ScopeKind

    await case.bindings.record_operation(OperationRecord("generated-location-name", "profile",
        OperationJournalState.COMPLETE, MappingProxyType({})))
    await case.bindings.set_scoped_name(person_id=PERSON, scope=location.room_scope,
        nickname="Generated Local Nick", operation_id="generated-location-name")
    await case.bindings.set_scoped_name(person_id=PERSON,
        scope=Scope(ScopeKind.ROOM, "signal/generated-other-location-room"),
        nickname="Generated Sibling Secret", operation_id="generated-location-name")
    out = await location.tools["where_is"]("Generated Local Nick")
    assert out["status"] == "ok", out
    global_name = await location.tools["where_is"]("Generated Global Presence")
    assert global_name["status"] == "not_found"
    absent = await location.tools["where_is"]("Generated Sibling Secret")
    assert absent["status"] == "not_found"
    assert "lat" not in absent


async def test_location_tool_rechecks_authority_after_the_sql_read(case, location, caplog):
    from dataclasses import replace

    from nora.location.adapters.selected_presence import SelectedPresenceStore
    from nora.location.adapters.tools import build

    class HoldAfterRead(SelectedPresenceStore):
        async def latest_fix(self, **kwargs):
            row = await super().latest_fix(**kwargs)
            await case.bindings.hold_identity(person_id=PERSON, operation_id="generated-late-hold")
            return row

    caplog.set_level("INFO", logger="nora.tools")
    source = HoldAfterRead(case.engine, access=location.access)
    tool = next(t.call for t in build(replace(location.deps, presence_source=source))
                if t.name == "where_is")
    out = await tool("Generated Global Presence")
    assert out["status"] == "unavailable"
    assert "lat" not in out and "Generated Global Presence" not in str(out)
    outcomes = [line for line in location.deps.metrics.render().splitlines()
                if line.startswith("nora_tool_calls_total{") and 'tool="where_is"' in line]
    assert len(outcomes) == 1 and 'status="unavailable"' in outcomes[0]
    logged = [record.getMessage() for record in caplog.records
              if record.getMessage().startswith("tool where_is status=")]
    assert len(logged) == 1 and "status=unavailable" in logged[0]


async def test_location_source_outage_is_unavailable_and_recovers_without_file_fallback(
    case, location,
):
    assert (await location.tools["where_is"]("Generated Global Presence"))["status"] == "ok"
    case.status = 503
    await case.collector.collect_once(case.circle, now_ms=int(time.time() * 1000))
    out = await location.tools["where_is"]("Generated Global Presence")
    assert out["status"] == "unavailable" and "lat" not in out
    case.status = 200
    await case.collector.collect_once(case.circle, now_ms=int(time.time() * 1000))
    assert (await location.tools["where_is"]("Generated Global Presence"))["status"] == "ok"


async def test_saved_places_use_selected_circle_and_cannot_survive_a_hold(case, location):
    from dataclasses import replace

    from tests.unit.test_presence_collector_synthetic import place

    await case.store.replace_places(circle_id=CIRCLE,
        places=[replace(place("generated-place", "Generated Private Home"), circle_id=CIRCLE)])
    out = await location.tools["saved_places"]()
    assert out["status"] == "ok" and "Generated Private Home" in str(out)
    await case.bindings.hold_identity(person_id=PERSON, operation_id="generated-hold-places")
    out = await location.tools["saved_places"]()
    assert out["status"] == "unavailable" and "Generated Private Home" not in str(out)


async def test_current_circle_reads_fold_old_fixes_without_rewriting_history(case, location):
    survivor = "generated-location-survivor"
    await case.bindings.register(person=ManagedPerson(survivor, PersonLifecycle.ACTIVE,
        display_name="Generated Surviving Presence"), account=ManagedAccount(str(uuid4()),
        survivor, "life360", frozenset({Identifier("life360", "member_id", "generated-peer")}),
        AccountState.BOUND))
    await case.registry.ensure_identity(external_id=survivor,
        traits={"display_name": "Generated Surviving Presence"})
    await case.bindings.consolidate(survivor=survivor, former=PERSON)
    await case.registry.set_state(external_id=PERSON, active=False)
    out = await location.tools["where_is"]("Generated Surviving Presence")
    assert out["status"] == "ok" and out["lat"] == 22.3, out
    assert [row["person_id"] for row in await _fixes(case)] == [PERSON]


async def test_public_weather_and_route_do_not_require_circle_availability(case, location):
    from dataclasses import replace

    from nora.location.adapters.tools import build
    from nora.testing.eval_backends import CannedRouteSource, CannedWeatherProvider

    case.status = 503
    await case.collector.collect_once(case.circle, now_ms=int(time.time() * 1000))
    deps = replace(location.deps, weather_source=CannedWeatherProvider(),
                   route_source=CannedRouteSource())
    tools = {tool.name: tool.call for tool in build(deps)}
    weather = await tools["weather"]()
    assert weather["status"] == "ok" and "30" in str(weather)
    route = await tools["route"]("Generated Public Station", "Generated Public Park")
    assert route["status"] == "ok", route


async def test_derived_place_route_rechecks_selected_current_identity(case, location):
    from dataclasses import replace

    from nora.infra.sql.mobility import SqlMobilityStore
    from nora.infra.sql.rollups import SqlRollupStore
    from nora.location.adapters.tools import build
    from nora.testing.eval_backends import CannedRouteSource

    rollups = SqlRollupStore(case.engine)
    build_id = await rollups.create_build(code_version="generated-test", params_sha256="a" * 64)
    mobility = SqlMobilityStore(case.engine).scoped(build_id)
    place_id = await mobility.save_place(subject_id=PERSON, circle_id=CIRCLE, place_id=None,
        lat=22.3, lng=114.1, radius_m=50, kind="home", confidence=0.9,
        label="Generated Private Home", saved_place_id=None, first_seen_ts=NOW, last_seen_ts=NOW)
    deps = replace(location.deps, mobility=mobility, route_source=CannedRouteSource())
    deps._bound_places["generated-bound-place"] = (place_id, "Generated Private Home")
    tool = next(t.call for t in build(deps) if t.name == "route")
    out = await tool("generated-bound-place", "Generated Public Park")
    assert out["status"] == "ok", out
    await case.bindings.revoke_binding(account=case.ident)
    out = await tool("generated-bound-place", "Generated Public Park")
    assert out["status"] != "ok" and "lat" not in out


async def test_nora_root_composition_connects_authenticated_current_circles_to_real_ory(
    case, location, example_config, clean_pg_url, tmp_path, ory,
):
    from dataclasses import replace

    from nora.agent.room import Room
    from nora.app.composition_stores import managed_identity
    from nora.config_identity import IdentityBackendConfig, ManagedOryConfig
    from nora.location.adapters.tools import build
    from nora.location.wire import presence
    from nora.obs.metrics import Metrics
    from nora.policy import load_policy
    from nora.testing import FakeRosterSource

    managed = ManagedOryConfig(kratos_admin_url=ory.kratos_admin,
        kratos_public_url=ory.kratos_public, keto_read_url=ory.keto_read,
        keto_write_url=ory.keto_write)
    policy_path = tmp_path / "generated-current-circle-policy.toml"
    policy_path.write_text('[[conversations]]\nplatform="signal"\nid="generated-location-room"\n'
        'label="Generated Location Room"\nunit="generated-location-unit"\npersons="*"\n')
    cfg = example_config.model_copy(update={
        "identity": IdentityBackendConfig(backend="managed", managed=managed),
        "identity_file": tmp_path / "absent-generated-location.toml", "policy_file": policy_path,
        "presence": PresenceConfig(enabled=True, managed_circle_ids=(CIRCLE,),
                                   refresh_url=str(location.server.make_url("/"))),
    })
    cfg._credentials = {cfg.store.database_url_env: clean_pg_url,
                        cfg.presence.directory_key_env: "generated-directory-key"}
    wired = await managed_identity(cfg, None, load_policy(policy_path), case.engine)
    try:
        outcome, remote = await wired.services.registry.ensure_identity(external_id=PERSON,
            traits={"display_name": "Generated Global Presence", "social": True})
        assert outcome.status.value == "complete" and remote
        await case.bindings.correlate_kratos(person_id=PERSON, kratos_identity_id=remote)
        access = wired.services.access
        selected = presence(cfg, case.engine, Metrics(), None, access)
        room = Room(platform="signal", conversation_id="generated-location-room", access=access,
            roster_source=FakeRosterSource(groups={"generated-location-room": (
                frozenset({"+85290000140"}),)}))
        deps = replace(location.deps, room=room, persons=access.persons_view(),
                       presence_source=selected.presence_store,
                       presence_circles=selected.presence_circles)
        tool = next(t.call for t in build(deps) if t.name == "where_is")
        assert (await tool("Generated Global Presence"))["status"] == "ok"
        members = await access.life360_memberships()
        assert [(m.person_id, m.member_id, m.circle_id) for m in members] == [
            (PERSON, MEMBER, CIRCLE)]
        await wired.services.registry.set_state(external_id=PERSON, active=False)
        assert (await tool("Generated Global Presence"))["status"] == "unavailable"
        assert not cfg.identity_file.exists()
    finally:
        await wired.session.close()


async def test_managed_refresh_wiring_authenticates_before_a_provider_ping(
    case, location, example_config,
):
    from nora.config_identity import IdentityBackendConfig, ManagedOryConfig
    from nora.location.wire import refresh

    cfg = example_config.model_copy(update={
        "identity": IdentityBackendConfig(backend="managed", managed=ManagedOryConfig(
            kratos_admin_url="http://127.0.0.1:4434", kratos_public_url="http://127.0.0.1:4433",
            keto_read_url="http://127.0.0.1:4466", keto_write_url="http://127.0.0.1:4467")),
        "presence": PresenceConfig(enabled=True, refresh_url=str(location.server.make_url("/"))),
    })
    cfg._credentials = {cfg.presence.directory_key_env: "generated-directory-key"}
    case.body = {"members": [_row(ts=int(time.time() * 1000) + 1000)]}
    refresher = refresh(cfg, location.store).refresher
    assert await refresher.refresh(person_id=PERSON)
    assert case.pings == [(CIRCLE, MEMBER)]
    cfg._credentials = {cfg.presence.directory_key_env: "generated-wrong-directory-key"}
    assert not await refresh(cfg, location.store).refresher.refresh(person_id=PERSON)
    assert case.pings == [(CIRCLE, MEMBER)]


async def test_scoped_circle_names_share_the_managed_ownership_read_boundary(case, location):
    from nora.people.app.managed import ManagedServices

    entered, release = asyncio.Event(), asyncio.Event()

    class ParkedNames(SqlBindingStore):
        async def scoped_name_for(self, *, person_id, scope):
            entered.set()
            await release.wait()
            return await super().scoped_name_for(person_id=person_id, scope=scope)

    original = location.services
    selected = ManagedServices(ParkedNames(case.engine), original.backend, original.topology,
        original.registry, original.links,
        circle_observations=location.observations).access
    read = asyncio.create_task(selected.life360_refs(scope=location.room_scope))
    await asyncio.wait_for(entered.wait(), 5)
    probe = sa.select(sa.func.pg_try_advisory_xact_lock(
        sa.func.hashtextextended(MANAGED_MUTATION_LOCK, 0)))
    try:
        async with case.engine.begin() as connection:
            assert (await connection.execute(probe)).scalar_one() is False
    finally:
        release.set()
        refs = await asyncio.wait_for(read, 10)
    assert [(row.person_id, ref.name) for row, ref in refs] == [
        (PERSON, "Generated Global Presence")]
