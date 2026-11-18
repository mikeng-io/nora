"""The installation-only admission command against real PostgreSQL, Kratos and Keto."""

import aiohttp

from nora.config_identity import ManagedOryConfig
from nora.domain.managed import (
    CheckResult,
    OutcomeStatus,
    Verdict,
    room_scope_id,
)
from nora.domain.managed_ports import ScopedRelation
from nora.domain.management import Scope, ScopeKind
from nora.infra.ory.keto import KetoClient
from nora.infra.ory.kratos import KratosClient
from nora.infra.sql import create_sql_engine
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.adapters.keto_authority import KetoAuthorityBackend
from nora.people.app.managed_admission import plan
from nora.people.wire import managed_services
from nora.persons import load_persons
from nora.policy import load_policy
from scripts.managed_identity_handoff import COMPLETE, REFUSED, RETRYABLE, run
from tests.integration.managed_ory import keto as keto
from tests.integration.managed_ory import ory as ory
from tests.unit.test_managed_admission import IDENTITY, POLICY

EXPLICIT_POLICY = POLICY.replace('persons = "*"', 'persons = ["miles", "ada", "listed_only"]')


def _arguments(command, identity, policy, database, endpoints):
    return [
        command, "--identity", str(identity), "--policy", str(policy),
        "--database-url", database, "--kratos-admin-url", endpoints.kratos_admin,
        "--keto-read-url", endpoints.keto_read, "--keto-write-url", endpoints.keto_write,
    ]


async def _assert_imported_mapping(services, persons):
    for person_id in persons.by_person:
        assert await services.bindings.person(person_id) is not None
        state = await services.registry.identity_state(external_id=person_id)
        assert state.verdict is Verdict.ALLOWED
        for account in persons.accounts_for(person_id):
            observed_ids = set()
            for identifier in account.identifiers:
                observed = await services.bindings.record_for(account=identifier)
                assert observed is not None and observed.person_id == person_id
                assert observed.identifiers == account.identifiers
                assert observed.label == account.label
                observed_ids.add(observed.account_id)
            assert len(observed_ids) == 1
    assert len(await services.bindings.accounts_for("miles")) == 3
    assert await services.bindings.canonical_person("miles_old") == "miles"
    outcome, traits = await services.registry.identity_traits(external_id="miles")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert traits is not None and traits["social"] is False
    for platform, room, person, expected in (
        ("signal", "generated-room-a", "miles", Verdict.ALLOWED),
        ("signal", "generated-room-a", "ada", Verdict.DENIED),
        ("signal", "generated-room-b", "miles", Verdict.ALLOWED),
        ("discord", "generated-g1/c1", "miles", Verdict.DENIED),
        ("discord", "generated-g1/c1", "ada", Verdict.ALLOWED),
    ):
        scope = Scope(ScopeKind.ROOM, room_scope_id(platform, room))
        assert (await services.authority.check(
            scope=scope, person_id=person, permit="view")).verdict is expected


async def test_initial_admission_uses_real_ory_and_retains_the_exact_source(
    clean_pg_url, tmp_path, ory, capsys,
):
    identity = tmp_path / "identity.toml"
    policy = tmp_path / "policy.toml"
    identity.write_text(IDENTITY)
    policy.write_text(EXPLICIT_POLICY)
    persons = load_persons(identity)
    engine = create_sql_engine(clean_pg_url)
    store = SqlBindingStore(engine)
    def arguments(command):
        return _arguments(command, identity, policy, clean_pg_url, ory)
    try:
        assert await run(arguments("plan")) == COMPLETE
        assert not await store.persons()
        assert await run(arguments("apply")) == COMPLETE
        async with aiohttp.ClientSession() as session:
            services = managed_services(
                managed=ManagedOryConfig(
                    kratos_admin_url=ory.kratos_admin, keto_read_url=ory.keto_read,
                    keto_write_url=ory.keto_write,
                ),
                session=session, policy=load_policy(policy), engine=engine, credentials={},
            )
            try:
                await _assert_imported_mapping(services, persons)
            finally:
                await services.aclose()
        before = tuple(await store.accounts_for("miles"))
        assert await run(arguments("apply")) == COMPLETE
        assert tuple(await store.accounts_for("miles")) == before
        identity.write_text(IDENTITY + "\n# changed source\n")
        assert await run(arguments("apply")) == REFUSED
        assert tuple(await store.accounts_for("miles")) == before
        identity.write_text(IDENTITY)
        journal = await store.operation(plan(identity, policy).import_id)
        assert journal is not None and "sources" in journal.snapshot
        assert journal.snapshot["sources"] == {
            "identity_toml": IDENTITY, "policy_toml": EXPLICIT_POLICY,
        }
        assert not await store.identity_held("miles")
        output = capsys.readouterr().out
        assert "target: fresh" in output and "refused: different-import" in output
        assert "+852" not in output and "generated-room-a" not in output
    finally:
        await engine.dispose()


async def test_a_target_connected_plan_refuses_the_held_external_id_real_kratos_shows(
    clean_pg_url, tmp_path, ory, capsys,
):
    """The SAME preflight read apply runs, against the real admin API: an
    identity already holding an imported person's `external_id` refuses the
    fresh claim before a single write — through the command, for plan and
    apply alike."""
    identity = tmp_path / "identity.toml"
    policy = tmp_path / "policy.toml"
    identity.write_text(IDENTITY)
    policy.write_text(EXPLICIT_POLICY)
    engine = create_sql_engine(clean_pg_url)
    store = SqlBindingStore(engine)
    try:
        async with aiohttp.ClientSession() as session:
            outcome, foreign = await KratosClient(
                session=session, admin_url=ory.kratos_admin,
            ).create_identity(
                external_id="miles", traits={"display_name": "generated-foreign"},
            )
            assert outcome.status is OutcomeStatus.COMPLETE and foreign is not None
        assert await run(_arguments(
            "plan", identity, policy, clean_pg_url, ory)) == REFUSED
        assert await run(_arguments(
            "apply", identity, policy, clean_pg_url, ory)) == REFUSED
        assert not await store.persons()
        output = capsys.readouterr().out
        assert "refused: kratos-correlation-present" in output
        assert "miles" not in output and "generated-foreign" not in output
    finally:
        await engine.dispose()


async def test_admission_refuses_a_foreign_subject_set_the_real_adapter_sees(
    clean_pg_url, tmp_path, ory,
):
    """Real Keto: a containment row the import does not own is INVISIBLE to
    `relations_on`'s person-only view — the complete read must refuse the
    claim before any write, and a userset subject_set shape is unavailable,
    never a silently dropped row."""
    identity = tmp_path / "identity.toml"
    policy = tmp_path / "policy.toml"
    identity.write_text(IDENTITY)
    policy.write_text(EXPLICIT_POLICY)
    engine = create_sql_engine(clean_pg_url)
    store = SqlBindingStore(engine)
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    arguments = _arguments("apply", identity, policy, clean_pg_url, ory)
    try:
        async with aiohttp.ClientSession() as session:
            backend = KetoAuthorityBackend(KetoClient(
                session=session,
                read_url=ory.keto_read, write_url=ory.keto_write,
            ))
            # A foreign parent on a declared room: grants on that parent
            # would flow down to every member the import writes.
            outcome = await backend.publish_containment(
                scope=room_a,
                parent=Scope(ScopeKind.UNIT, "generated-foreign-unit"))
            assert outcome.status is OutcomeStatus.COMPLETE
            # The REAL adapter's complete read returns the subject-set row
            # its person-only view cannot show.
            rows = await backend.scope_relations_on(scope=room_a)
            assert not isinstance(rows, CheckResult)
            assert ScopedRelation(
                "unit", Scope(ScopeKind.UNIT, "generated-foreign-unit")) in rows
            assert await backend.relations_on(scope=room_a) == frozenset()
        assert await run(arguments) == REFUSED
        assert not await store.persons()
        # A `subject_set` carrying its own relation (userset "members of")
        # cannot be represented — the read is unavailable, never subset.
        async with aiohttp.ClientSession() as session:
            response = await session.put(
                f"{ory.keto_write}/admin/relation-tuples",
                json={
                    "namespace": "Room", "object": room_a.id,
                    "relation": "member",
                    "subject_set": {
                        "namespace": "Unit", "object": "family",
                        "relation": "member",
                    },
                })
            assert response.status == 201
        assert await run(arguments) == RETRYABLE
        assert not await store.persons()
    finally:
        await engine.dispose()


async def test_the_real_adapter_returns_every_subject_set_scope_relation(keto):
    """Real Keto: `scope_relations_on` returns member AND subject-set rows
    `relations_on` cannot show — and a userset `subject_set` (a relation
    carried inside it) is `unavailable`, never a silently dropped row."""
    read_url, write_url = keto
    room = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    parent = Scope(ScopeKind.UNIT, "generated-foreign-unit")
    async with aiohttp.ClientSession() as session:
        backend = KetoAuthorityBackend(KetoClient(
            session=session, read_url=read_url, write_url=write_url,
        ))
        outcome = await backend.put_relation(
            scope=room, relation="member", person_id="generated-member")
        assert outcome.status is OutcomeStatus.COMPLETE
        outcome = await backend.publish_containment(scope=room, parent=parent)
        assert outcome.status is OutcomeStatus.COMPLETE
        rows = await backend.scope_relations_on(scope=room)
        assert rows == frozenset({
            ScopedRelation("member", "generated-member"),
            ScopedRelation("unit", parent),
        })
        assert await backend.relations_on(scope=room) == frozenset(
            {("member", "generated-member")})
        response = await session.put(
            f"{write_url}/admin/relation-tuples",
            json={
                "namespace": "Room", "object": room.id, "relation": "member",
                "subject_set": {
                    "namespace": "Unit", "object": "family",
                    "relation": "member",
                },
            })
        assert response.status == 201
        unreadable = await backend.scope_relations_on(scope=room)
        assert isinstance(unreadable, CheckResult)
        assert unreadable.verdict is Verdict.UNAVAILABLE
