"""The managed-to-file export command against real PostgreSQL, Kratos and
Keto — the SAME managed state the initial admission published, read back
out into a fresh bundle by the REAL command (its own `_target_services`,
no seam), proven by the generated pair loading and holding the admitted
mapping exactly."""

import uuid

import pytest

from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql import create_sql_engine
from nora.persons import load_persons
from nora.policy import load_policy
from scripts.managed_identity_export import COMPLETE, run
from scripts.managed_identity_handoff import run as admit_run
from tests.integration.managed_ory import ory as ory
from tests.unit.test_managed_admission import IDENTITY, POLICY

# The Ory admission test's explicit form: no `*` room — the export's
# open-room rule is covered by the unit and SQL suites; this run measures
# the real store/graph wiring, not that rule again.
EXPLICIT_POLICY = POLICY.replace(
    'persons = "*"', 'persons = ["miles", "ada", "listed_only"]')


@pytest.fixture
def sources(tmp_path):
    identity = tmp_path / "identity.toml"
    policy = tmp_path / "policy.toml"
    identity.write_text(IDENTITY)
    policy.write_text(EXPLICIT_POLICY)
    return identity, policy


async def test_export_rebuilds_the_admitted_mapping_over_real_ory(
    clean_pg_url, tmp_path, ory, sources, capsys,
):
    identity, policy = sources
    engine = create_sql_engine(clean_pg_url)
    try:
        # Admit for real — the full managed state the export then inverts.
        assert await admit_run([
            "apply", "--identity", str(identity), "--policy", str(policy),
            "--database-url", clean_pg_url,
            "--kratos-admin-url", ory.kratos_admin,
            "--keto-read-url", ory.keto_read,
            "--keto-write-url", ory.keto_write,
        ]) == COMPLETE

        output = tmp_path / f"export-{uuid.uuid4().hex[:8]}"
        argv = [
            "--policy", str(policy), "--database-url", clean_pg_url,
            "--kratos-admin-url", ory.kratos_admin,
            "--keto-read-url", ory.keto_read,
        ]
        # plan first — validates everything, writes nothing.
        assert await run(["plan", *argv]) == COMPLETE
        assert not output.exists()
        assert await run(["export", *argv, "--output", str(output)]) == COMPLETE
        assert sorted(p.name for p in output.iterdir()) == [
            "export-manifest.json", "identity.toml", "policy.toml",
        ]

        persons = load_persons(output / "identity.toml")
        generated = load_policy(output / "policy.toml")
        source = load_persons(identity)
        # The same people, the same stable references, the same accounts.
        assert persons.subject_id_for("miles_old") == "miles"
        assert persons.social_off("miles")
        # Identity-source people plus `listed_only`, the policy member with
        # no account block — exported with none, never a phantom grouping.
        assert set(persons.by_person) == {*source.by_person, "listed_only"}
        # The whole account grouping round-trips: every exported person
        # carries exactly the source's accounts — platform, label and the
        # full typed-identifier set — never merged, split or re-typed.
        def groupings(holder, person_id):
            return {
                (a.platform, a.label, frozenset(
                    (i.kind, i.value) for i in a.identifiers))
                for a in holder.accounts_for(person_id)
            }
        for person_id in source.by_person:
            assert groupings(persons, person_id) == groupings(source, person_id)
        assert persons.accounts_for("listed_only") == ()
        # The emitted rooms hold exactly what Keto answers.
        room_a = generated.conversation("signal", "generated-room-a")
        room_b = generated.conversation("signal", "generated-room-b")
        room_c = generated.conversation("discord", "generated-g1/c1")
        assert room_a.persons == frozenset({"miles"})
        assert room_b.persons == frozenset({"miles", "ada", "listed_only"})
        assert room_c.persons == frozenset({"ada", "listed_only"})
        # The exported predicate agrees with managed on the spot checks the
        # admission test itself asserts.
        from nora.access import IdentityAccess
        file_access = IdentityAccess.for_files(persons, generated)
        for scope_id, person_id, want in (
            (room_scope_id("signal", "generated-room-a"), "miles", True),
            (room_scope_id("signal", "generated-room-a"), "ada", False),
            (room_scope_id("discord", "generated-g1/c1"), "ada", True),
        ):
            verdict = await file_access.check(
                scope=Scope(ScopeKind.ROOM, scope_id),
                person_id=person_id, permit="speak")
            assert verdict.allowed == want
        out = capsys.readouterr().out
        assert "export:" in out and "export: complete" in out
        # Bounded output only — never identifiers or room names.
        assert "+852" not in out and "generated-room-a" not in out
    finally:
        await engine.dispose()


async def test_export_refuses_when_kratos_cannot_answer(
    clean_pg_url, tmp_path, ory, sources, capsys,
):
    """Kratos up for the admission, then gone for the export: the command
    reports unavailable (retryable), never a guessed bundle."""
    identity, policy = sources
    engine = create_sql_engine(clean_pg_url)
    try:
        assert await admit_run([
            "apply", "--identity", str(identity), "--policy", str(policy),
            "--database-url", clean_pg_url,
            "--kratos-admin-url", ory.kratos_admin,
            "--keto-read-url", ory.keto_read,
            "--keto-write-url", ory.keto_write,
        ]) == COMPLETE
        rc = await run([
            "plan", "--policy", str(policy),
            "--database-url", clean_pg_url,
            "--kratos-admin-url", "http://127.0.0.1:1",  # nothing answers
            "--keto-read-url", ory.keto_read,
        ])
        assert rc == 3
        out = capsys.readouterr().out
        assert "unavailable" in out
    finally:
        await engine.dispose()


async def test_export_refuses_an_extra_parent_returned_by_the_real_keto_adapter(
    clean_pg_url, tmp_path, ory, sources, capsys,
):
    from aiohttp import ClientSession

    from nora.infra.ory import KetoClient
    from nora.people.adapters.keto_authority import KetoAuthorityBackend

    identity, policy = sources
    assert await admit_run(["apply", "--identity", str(identity), "--policy", str(policy),
        "--database-url", clean_pg_url, "--kratos-admin-url", ory.kratos_admin,
        "--keto-read-url", ory.keto_read, "--keto-write-url", ory.keto_write]) == COMPLETE
    argv = ["--policy", str(policy), "--database-url", clean_pg_url,
            "--kratos-admin-url", ory.kratos_admin, "--keto-read-url", ory.keto_read]
    positive = tmp_path / "generated-positive"
    assert await run(["export", *argv, "--output", str(positive)]) == COMPLETE
    capsys.readouterr()
    async with ClientSession() as session:
        backend = KetoAuthorityBackend(KetoClient(session=session,
            read_url=ory.keto_read, write_url=ory.keto_write))
        result = await backend.publish_containment(
            scope=Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a")),
            parent=Scope(ScopeKind.UNIT, "generated-foreign-export-parent"))
        assert result.status.value == "complete"
    output = tmp_path / "generated-refused"
    assert await run(["export", *argv, "--output", str(output)]) == 2
    assert not output.exists()
    printed = capsys.readouterr().out
    assert "unfaithful:subject-set-relation" in printed
    assert "generated-foreign-export-parent" not in printed and str(output) not in printed
