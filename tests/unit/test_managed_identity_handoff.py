"""The `managed_identity_handoff` command — plan/apply/refusal/exit codes —
through the real `run` entry point over the managed fakes."""

import argparse
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from nora.domain.managed import (
    OperationJournalState,
    Outcome,
    Verdict,
    room_scope_id,
    unavailable,
)
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.managed_identity import SqlBindingStore
from nora.people.adapters.keto_authority import KetoAuthorityBackend
from nora.people.adapters.managed_handoff import KratosAdmission, SqlAdmissionProbe
from nora.people.app.managed_admission import FileAdmission, prepare
from nora.people.app.managed_authority import AuthorityService
from nora.testing.managed import (
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeTopologySource,
)
from nora.testing.managed_authority import FakeAuthorityBackend
from scripts.managed_identity_handoff import (
    COMPLETE,
    REFUSED,
    RETRYABLE,
    _target_services,
    run,
)
from tests.fakes import FakeAdmissionKratos, FakeAdmissionProbe

IDENTITY = """\
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "+85200000001"
label = "personal"
"""

POLICY = """\
default = "deny"

[[conversations]]
platform = "signal"
id = "generated-room-a"
unit = "family"
label = "Room A"
persons = ["miles"]

[units.family]
workspace = "home"
"""


@pytest.fixture
def sources(tmp_path):
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(IDENTITY)
    policy_path.write_text(POLICY)
    return identity_path, policy_path


def _services(store=None, backend=None, registry=None):
    store = store or FakeBindingStore()
    backend = backend or FakeAuthorityBackend()
    registry = registry or FakeIdentityRegistry()

    @asynccontextmanager
    async def factory():
        yield FileAdmission(
            bindings=store, backend=backend,
            kratos=FakeAdmissionKratos(registry.identities),
            probe=FakeAdmissionProbe(store),
        )
    return factory, store, backend


async def test_plan_prints_the_import_and_never_touches_a_target(sources):
    identity_path, policy_path = sources
    rc = await run([
        "plan", "--identity", str(identity_path), "--policy", str(policy_path),
    ])
    assert rc == COMPLETE


async def test_plan_refuses_an_unsupported_input(sources, capsys):
    _, policy_path = sources
    policy_path.write_text(POLICY + '\n[dm]\npersons = ["miles"]\n')
    rc = await run([
        "plan", "--identity", str(sources[0]), "--policy", str(policy_path),
    ])
    assert rc == REFUSED
    assert "unsupported-input:dm-admission" in capsys.readouterr().out


async def test_apply_reports_complete_and_imports(sources):
    factory, store, _ = _services()
    identity_path, policy_path = sources
    rc = await run([
        "apply", "--identity", str(identity_path), "--policy", str(policy_path),
        "--database-url", "generated", "--kratos-admin-url", "generated",
        "--keto-read-url", "generated", "--keto-write-url", "generated",
    ], services=factory)
    assert rc == COMPLETE
    assert "miles" in store.people


async def test_imported_file_authority_lands_as_scoped_access(sources):
    """The out-of-band bootstrap's own proof: `apply` through the real CLI
    entry wrote the containment chain and the `member` tuples, so the
    authoritative `check` afterwards answers the imported person's `view`
    on their room — and nothing beyond it: no management permit, no
    upward unit read, nothing for a person the file never named."""
    factory, store, backend = _services()
    identity_path, policy_path = sources
    rc = await run([
        "apply", "--identity", str(identity_path), "--policy", str(policy_path),
        "--database-url", "generated", "--kratos-admin-url", "generated",
        "--keto-read-url", "generated", "--keto-write-url", "generated",
    ], services=factory)
    assert rc == COMPLETE
    room = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    unit = Scope(ScopeKind.UNIT, "family")
    authority = AuthorityService(
        store, backend,
        FakeTopologySource(
            rooms={room.id: (unit.id, False)},
            units={unit.id: "home"},
            workspaces={"home"},
        ),
    )
    assert (await authority.check(
        scope=room, person_id="miles", permit="view")
    ).verdict is Verdict.ALLOWED
    assert (await authority.check(
        scope=room, person_id="miles", permit="manage_members")
    ).verdict is Verdict.DENIED
    assert (await authority.check(
        scope=unit, person_id="miles", permit="view")
    ).verdict is Verdict.DENIED
    assert (await authority.check(
        scope=room, person_id="person_never", permit="view")
    ).verdict is Verdict.DENIED


async def test_apply_refuses_missing_target_flags(sources):
    identity_path, policy_path = sources
    rc = await run([
        "apply", "--identity", str(identity_path), "--policy", str(policy_path),
        "--database-url", "generated",
    ], services=_services()[0])
    assert rc == REFUSED


class _NoWrites(FakeAuthorityBackend):
    """Reads answer; relation writes report an outage — the authority step
    fails mid-journal while the claim probe still passes."""

    def __init__(self) -> None:
        super().__init__()
        self.writes_ok = False

    async def put_relation(self, **kwargs):
        if not self.writes_ok:
            return unavailable("outage")
        return await super().put_relation(**kwargs)


async def test_apply_resume_reports_complete(sources):
    """An interrupted apply exits retryable; the SAME command finishes the
    import on the same stores."""
    backend = _NoWrites()
    factory, store, _ = _services(backend=backend)
    identity_path, policy_path = sources
    argv = [
        "apply", "--identity", str(identity_path), "--policy", str(policy_path),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x", "--keto-write-url", "x",
    ]
    first = await run(argv, services=factory)
    assert first == RETRYABLE
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE
    backend.writes_ok = True
    rc = await run(argv, services=factory)
    assert rc == COMPLETE
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.COMPLETE


async def test_apply_binds_the_effect_captured_before_setup(sources, capsys):
    """A source file changed between the plan-time capture and service
    setup cannot swap the effect — the durable archive, digests and
    memberships all describe the FIRST capture, and the next run simply
    names a different import."""
    identity_path, policy_path = sources
    factory, store, _ = _services()
    expected = prepare(identity_path, policy_path)
    assert not isinstance(expected, Outcome)

    @asynccontextmanager
    async def setup_time_change():
        policy_path.write_text(POLICY.replace(
            'persons = ["miles"]', 'persons = ["miles", "ada"]'))
        identity_path.write_text(IDENTITY.replace(
            'name = "Miles"', 'name = "Changed"'))
        async with factory() as admission:
            yield admission

    argv = [
        "apply", "--identity", str(identity_path), "--policy", str(policy_path),
        "--database-url", "generated", "--kratos-admin-url", "generated",
        "--keto-read-url", "generated", "--keto-write-url", "generated",
    ]
    assert await run(argv, services=setup_time_change) == COMPLETE
    journal = next(iter(store.operations.values()))
    assert journal.operation_id == expected.import_id
    assert dict(journal.snapshot) == dict(expected.snapshot)
    assert journal.snapshot["sources"] == {
        "identity_toml": IDENTITY, "policy_toml": POLICY}
    assert f"plan {journal.operation_id}" in capsys.readouterr().out
    # The changed files now name a DIFFERENT import — refused, not applied over.
    assert await run(argv, services=factory) == REFUSED
    assert len(store.operations) == 1


async def test_default_composition_never_reopens_the_source_paths(sources):
    """The real `_target_services` factory wires entirely from the captured
    effect — files destroyed after prepare cannot break or re-plan setup."""
    identity_path, policy_path = sources
    prepared = prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    identity_path.write_text("[[[ unparseable")
    policy_path.write_text("[[[ unparseable")
    args = argparse.Namespace(
        database_url="postgresql+asyncpg://generated.invalid/generated",
        kratos_admin_url="http://generated.invalid",
        keto_read_url="http://generated.invalid",
        keto_write_url="http://generated.invalid",
        kratos_admin_key_env="", keto_key_env="", timeout_seconds=5.0)
    async with _target_services(args, prepared) as admission:
        assert isinstance(admission.bindings, SqlBindingStore)
        assert isinstance(admission.backend, KetoAuthorityBackend)
        assert isinstance(admission.kratos, KratosAdmission)
        assert isinstance(admission.probe, SqlAdmissionProbe)


def test_plan_subprocess_uses_the_real_loaders(tmp_path):
    """The installed entry point itself, no service seam: real loaders,
    real exit code."""
    identity_path = tmp_path / "identity.toml"
    policy_path = tmp_path / "policy.toml"
    identity_path.write_text(IDENTITY)
    policy_path.write_text(POLICY + '\n[dm]\npersons = ["miles"]\n')
    root = Path(__file__).parents[2]
    result = subprocess.run(  # noqa: S603 - this interpreter, this repo's script
        [sys.executable, "scripts/managed_identity_handoff.py",
         "plan", "--identity", str(identity_path),
         "--policy", str(policy_path)],
        capture_output=True, text=True, cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert result.returncode == REFUSED
    assert "unsupported-input:dm-admission" in result.stdout
