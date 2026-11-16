"""The `managed_identity_handoff` command — plan/apply/refusal/exit codes —
through the real `run` entry point over the managed fakes."""

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from nora.domain.managed import OperationJournalState, unavailable
from nora.people.app.managed_admission import FileAdmission
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
)
from scripts.managed_identity_handoff import COMPLETE, REFUSED, RETRYABLE, run
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
