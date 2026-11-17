"""Generated SQL verifies selected analytics identities, historical circles and unit isolation."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.access import SocialPreferenceUnavailableError
from nora.analytics.composition import build_rollup
from nora.config_identity import IdentityBackendConfig, ManagedOryConfig
from nora.domain.managed import OutcomeStatus
from nora.domain.presence import PresenceFix, WireState
from nora.domain.rollup import NEVER
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.rollups import RecordMark
from nora.infra.sql.schema import PRESENCE_FIXES
from nora.people.app.managed import ManagedServices
from nora.policy import load_policy
from nora.testing.managed import FakeLinkRequests
from nora.testing.managed_authority import FakeAuthorityBackend
from tests.integration.managed_ory import ory as _ory
from tests.integration.test_character_social import T0 as SOCIAL_T0
from tests.integration.test_character_social import _record
from tests.integration.test_character_social import social as _sql_social
from tests.unit.test_managed_background_access import _background_identity_case

social = _sql_social
ory = _ory
pytestmark = pytest.mark.asyncio
T0 = int(SOCIAL_T0.timestamp() * 1000)


async def _case(engine, config, tmp_path, database_url):
    case = await _background_identity_case()
    bindings = SqlBindingStore(engine)
    for account in case.bindings.accounts.values():
        member = next(m for m in case.bindings.memberships.values()
                      if m.person_id == account.person_id)
        await bindings.register(person=case.bindings.people[account.person_id],
            account=replace(account, account_id=str(uuid4())), membership=member)
    case.bindings = bindings
    case.access = ManagedServices(bindings, FakeAuthorityBackend(), case.topology,
                                  case.registry, FakeLinkRequests()).access
    config = config.model_copy(deep=True, update={
        "analytics": config.analytics.model_copy(update={"enabled": True}),
        "identity": IdentityBackendConfig(backend="managed", managed=ManagedOryConfig(
            kratos_admin_url="http://127.0.0.1:4434", kratos_public_url="http://127.0.0.1:4433",
            keto_read_url="http://127.0.0.1:4466", keto_write_url="http://127.0.0.1:4467")),
        "identity_file": tmp_path / "absent-identity.toml",
        "policy_file": tmp_path / "policy.toml",
    })
    config._credentials = {config.store.database_url_env: database_url}
    config.policy_file.write_text(
        '[[conversations]]\nplatform="signal"\nid="g1"\nlabel="Generated Analytics Room"\n'
        'unit="generated-unit"\npersons="*"\n'
        '[[conversations]]\nplatform="signal"\nid="g3"\nlabel="Generated Other Analytics Room"\n'
        'unit="generated-profile-other"\n'
        'persons="*"\n')
    policy = load_policy(config.policy_file)
    return SimpleNamespace(case=case, cfg=config, policy=policy)


async def _fixes(engine, people, accuracy=10.0):
    store = PresenceStore(engine)
    for i, person in enumerate(people):
        assert await store.record_fix(fix=PresenceFix(circle_id=f"generated-circle-{i}",
            person_id=person, ts=T0, lat=22.0 + i * 0.01, lng=114.0,
            wire_state=WireState.LIVE, recorded_at=T0, accuracy_m=accuracy))


@pytest.mark.parametrize("accuracy", [None, 10.0])
async def test_selected_analytics_folds_recorded_circles_without_file_authority(
    social, example_config, clean_pg_url, tmp_path, accuracy,
):
    _, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    case = env.case
    original, survivor, outsider = case.people
    await _fixes(engine, case.people, accuracy)
    assert (await case.bindings.consolidate(survivor=survivor, former=original)).status is (
        OutcomeStatus.COMPLETE)
    await case.registry.set_state(external_id=original, active=False)
    deps = build_rollup(env.cfg, access=case.access, policy=env.policy, engine=engine)
    assert deps.persons is None and not env.cfg.identity_file.exists()
    with pytest.raises(RuntimeError, match="projection is unavailable"):
        tuple(deps.subjects)
    await deps.refresh_identity()
    subjects = {s.subject_id: s for s in deps.subjects}
    assert set(subjects) == {survivor, outsider}
    assert set(subjects[survivor].person_ids) == {original, survivor}
    assert set(subjects[survivor].circle_ids) == {"generated-circle-0", "generated-circle-1"}
    units = {u.unit: u for u in deps.units}
    assert units["generated-unit"].members == (survivor,)
    assert units["generated-profile-other"].members == (outsider,)
    assert units["generated-unit"].accounts[("signal", "+85290000110")] == survivor
    assert "+85290000112" not in {value for _, value in units["generated-unit"].accounts}
    try:
        build = await deps.runner.ensure_build(through_ms=T0 + 3_600_000)
        report = await deps.runner.run(build_id=build, through_ms=T0 + 3_600_000)
    except ValueError as exc:
        raise AssertionError("unadmitted fixes must not turn an unknown clock into a date") from exc
    assert not report.failed
    assert set(report.folds) == {survivor, outsider}
    assert all(fold.events == 0 for fold in report.folds.values())
    assert {fold.logical_now_ms for fold in report.folds.values()} == (
        {NEVER} if accuracy is None else {T0})
    assert {u.unit for u in report.units} == {"generated-unit", "generated-profile-other"}
    async with engine.connect() as conn:
        assert set((await conn.execute(sa.select(PRESENCE_FIXES.c.person_id))).scalars()) == (
            set(case.people))


async def test_managed_analytics_invalidates_outage_and_recovers_current_members(
    social, example_config, clean_pg_url, tmp_path,
):
    _, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    await _fixes(engine, env.case.people)
    deps = build_rollup(env.cfg, access=env.case.access, policy=env.policy, engine=engine)
    await deps.refresh_identity()
    assert len(deps.subjects) == 3
    env.case.registry.down = True
    with pytest.raises(SocialPreferenceUnavailableError):
        await deps.refresh_identity()
    with pytest.raises(RuntimeError, match="projection is unavailable"):
        tuple(deps.subjects)
    with pytest.raises(RuntimeError, match="projection is unavailable"):
        tuple(deps.units)
    env.case.registry.down = False
    await deps.refresh_identity()
    assert len(deps.subjects) == 3
    from tests.unit.test_scoped_engagement_names import _M_ROOM
    await env.case.bindings.set_membership(person_id=env.case.people[0], scope=_M_ROOM,
                                          member=False, source="generated")
    await deps.refresh_identity()
    assert next(u for u in deps.units if u.unit == "generated-unit").members == (
        env.case.people[1],)
    peer = env.case.people[1]
    assert (await env.case.bindings.hold_identity(
        person_id=peer, operation_id="generated-analytics-hold")).status is OutcomeStatus.COMPLETE
    await deps.refresh_identity()
    assert peer not in {s.subject_id for s in deps.subjects}
    assert not next(u for u in deps.units if u.unit == "generated-unit").members
    await env.case.bindings.release_identity_hold(
        person_id=peer, operation_id="generated-analytics-hold")
    await deps.refresh_identity()
    assert peer in {s.subject_id for s in deps.subjects}


async def test_composed_analytics_social_reads_obey_current_opt_out(
    social, example_config, clean_pg_url, tmp_path,
):
    store, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    person = env.case.people[0]
    await _record(store, "generated-analytics-private", person=person)
    deps = build_rollup(env.cfg, access=env.case.access, policy=env.policy, engine=engine)
    await deps.refresh_identity()
    mark = RecordMark(through_ms=int((await store.now()).timestamp() * 1000) + 1000,
                      message_id=0, reaction_id=0, edit_id=0)
    reads = deps.runner._units
    visible = await reads._posted([person], mark)
    assert len(visible.items) == 1 and visible.items[0].person_id == person
    env.case.registry.identities[person]["traits"]["social"] = False
    hidden = await reads._posted([person], mark)
    assert not hidden.items
    assert not hidden.observed_from
    env.case.registry.identities[person]["traits"]["social"] = True
    assert len((await reads._posted([person], mark)).items) == 1


async def test_daemon_refresh_refusal_keeps_watermark_and_health_honest(
    social, example_config, clean_pg_url, tmp_path, monkeypatch, aiohttp_client,
):
    from nora.analytics import __main__ as daemon
    from nora.infra.sql.rollups import SqlRollupStore

    _, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    await _fixes(engine, env.case.people)
    deps = build_rollup(env.cfg, access=env.case.access, policy=env.policy, engine=engine)
    clock = [T0 / 1000 + 3_600]
    await deps.refresh_identity()
    monkeypatch.setattr(daemon.time, "time", lambda: clock[0])
    last, first = {}, {}
    await daemon._tick(deps, last, first)
    assert set(last) == set(env.case.people)
    before = await SqlRollupStore(engine).current_build()
    assert before is not None
    client = await aiohttp_client(daemon._metrics_app(deps))
    good = await client.get("/health")
    assert good.status == 200 and (await good.json())["subjects"] == 3
    env.case.registry.down = True
    clock[0] += 30
    await daemon._tick(deps, last, first)
    assert await SqlRollupStore(engine).current_build() == before
    assert all(value == first[person] for person, value in last.items())
    assert deps.metrics.get("nora_analytics_rollup_age_seconds", subject=env.case.people[0]) == 30
    refused = await client.get("/health")
    assert refused.status == 503
    assert (await refused.json())["subjects"] is None
    env.case.registry.down = False
    await daemon._tick(deps, last, first)
    assert (await client.get("/health")).status == 200
    assert all(value == clock[0] for value in last.values())


async def test_managed_daemon_factory_uses_real_private_ory_and_a_fresh_engine(
    social, example_config, clean_pg_url, tmp_path, ory,
):
    from aiohttp import ClientSession

    from nora.analytics.composition import AnalyticsStartupError, open_rollup
    from nora.people.wire import managed_services

    _, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    await _fixes(engine, env.case.people)
    managed = ManagedOryConfig(kratos_admin_url=ory.kratos_admin,
        kratos_public_url=ory.kratos_public, keto_read_url=ory.keto_read,
        keto_write_url=ory.keto_write, timeout_seconds=15)
    cfg = env.cfg.model_copy(update={"identity": env.cfg.identity.model_copy(
        update={"managed": managed})})
    with pytest.raises(AnalyticsStartupError, match="authority failed"):
        async with open_rollup(cfg):
            pytest.fail("missing managed identities cannot borrow file people")
    async with ClientSession() as session:
        services = managed_services(managed=managed, session=session,
            policy=env.policy, engine=engine, credentials={})
        for i, person in enumerate(env.case.people):
            outcome, remote = await services.registry.ensure_identity(external_id=person,
                traits={"display_name": f"Generated Analytics Identity {i}", "social": True})
            assert outcome.status is OutcomeStatus.COMPLETE and remote
            assert (await services.bindings.correlate_kratos(
                person_id=person, kratos_identity_id=remote)).status is OutcomeStatus.COMPLETE
    async with open_rollup(cfg) as deps:
        assert deps.engine is not engine
        assert not cfg.identity_file.exists()
        assert {s.subject_id for s in deps.subjects} == set(env.case.people)
        assert {u.unit for u in deps.units} == {"generated-unit", "generated-profile-other"}
        assert deps.reload_files.status() == {"stale": False}


async def test_file_analytics_factory_preserves_its_own_subjects_and_circles(
    social, example_config, clean_pg_url, tmp_path,
):
    from nora.analytics.composition import open_rollup

    _, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    env.cfg.identity_file.write_text(
        '[persons.generated_file]\nname="Generated File Analytics"\n'
        '[[persons.generated_file.life360]]\nmember_id="generated-file-member"\n'
        'circle_id="generated-file-circle"\n')
    cfg = env.cfg.model_copy(update={"identity": IdentityBackendConfig()})
    async with open_rollup(cfg) as deps:
        assert deps.refresh_identity is None
        assert deps.persons is not None
        assert [(s.subject_id, s.circle_ids) for s in deps.subjects] == [
            ("generated_file", ("generated-file-circle",))]
        assert not ({s.subject_id for s in deps.subjects} & set(env.case.people))


@pytest.mark.parametrize("history", [100, 10_000])
async def test_circle_inventory_batches_generated_history_into_one_read(social, history):
    from sqlalchemy import event

    _, engine = social
    people = [f"generated-history-{i}" for i in range(100)]
    async with engine.begin() as conn:
        await conn.execute(sa.insert(PRESENCE_FIXES), [{
            "person_id": people[i % 100], "circle_id": f"generated-circle-{i % 100}",
            "ts": T0 + i, "lat": 22.0, "lng": 114.0, "wire_state": "live",
            "recorded_at": T0 + i, "accuracy_m": 10.0,
        } for i in range(history)])
    queries = []

    def observed(conn, cursor, statement, params, context, many):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", observed)
    try:
        found = await PresenceStore(engine).recorded_circles(people)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observed)
    assert len(found) == 100
    assert found[people[17]] == ("generated-circle-17",)
    assert len(queries) == 1
    async with engine.connect() as conn:
        stmt = sa.select(PRESENCE_FIXES.c.person_id, PRESENCE_FIXES.c.circle_id).where(
            PRESENCE_FIXES.c.person_id.in_(people)).distinct()
        sql = str(stmt.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
        plan = (await conn.execute(sa.text(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql))).scalar()
    nodes = [plan[0]["Plan"]]
    for node in nodes:
        nodes.extend(node.get("Plans", []))
    scanned = sum(n["Actual Rows"] * n["Actual Loops"] for n in nodes
                  if n.get("Relation Name") == "presence_fixes")
    assert scanned >= history
    print(f"retained-history={history} metadata-read-statements=1 source-rows={scanned}")


async def test_analytics_rechecks_consent_after_the_sql_social_read(
    social, example_config, clean_pg_url, tmp_path,
):
    from nora.analytics.units import PersonScope, UnitConfig, UnitRollups
    from nora.infra.sql.rollups import SqlRollupStore
    from nora.infra.sql.social_stream import SqlSocialStreamStore

    store, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    person = env.case.people[0]
    await _record(store, "generated-analytics-late-private", person=person)

    class RevokeOnRead(SqlSocialStreamStore):
        read_count = 0

        async def posted(self, **kwargs):
            read = await super().posted(**kwargs)
            self.read_count = len(read.items)
            env.case.registry.identities[person]["traits"]["social"] = False
            return read

    source = RevokeOnRead(engine)
    units = UnitRollups(store=SqlRollupStore(engine), config=UnitConfig(), units=(),
        people={person: PersonScope(subject=person)}, social=source, access=env.case.access)
    result = await units._posted([person], RecordMark(
        through_ms=int((await store.now()).timestamp() * 1000) + 1000,
        message_id=0, reaction_id=0, edit_id=0))
    assert source.read_count == 1
    assert not result.items and not result.observed_from


async def test_empty_eligible_analytics_cannot_hide_an_unavailable_authority(
    social, example_config, clean_pg_url, tmp_path, ory, aiohttp_server,
):
    from aiohttp import web

    from nora.analytics.composition import AnalyticsStartupError, open_rollup

    _, engine = social
    env = await _case(engine, example_config, tmp_path, clean_pg_url)
    for person in env.case.people:
        assert (await env.case.bindings.hold_identity(person_id=person,
            operation_id="generated-empty-analytics")).status is OutcomeStatus.COMPLETE
    requests = []

    async def unavailable(request):
        requests.append(request.path)
        return web.json_response({"error": "generated-unavailable"}, status=503)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", unavailable)
    server = await aiohttp_server(app)
    managed = ManagedOryConfig(kratos_admin_url=ory.kratos_admin,
        kratos_public_url=ory.kratos_public, keto_read_url=str(server.make_url("/")),
        keto_write_url=ory.keto_write, timeout_seconds=15)
    cfg = env.cfg.model_copy(update={"identity": env.cfg.identity.model_copy(
        update={"managed": managed})})
    with pytest.raises(AnalyticsStartupError, match="authority failed"):
        async with open_rollup(cfg):
            pass
    assert requests
