"""The `managed_identity_export` command — plan/export rehearsal, exit
codes and the fresh-destination rule — through the real `run` entry point
over the managed fakes."""

import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.adapters.file_backend import FileTopologySource
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_export import ManagedExport
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing.managed import (
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
)
from nora.testing.managed_authority import FakeAuthorityBackend
from scripts.managed_identity_export import COMPLETE, REFUSED, RETRYABLE, run
from tests.unit.test_managed_export import _Probe

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

ROOM_A = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))


@pytest.fixture
def sources(tmp_path):
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(POLICY)
    return policy_path


def _seeded():
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    store.people["miles"] = ManagedPerson(
        person_id="miles", lifecycle=PersonLifecycle.ACTIVE,
        kratos_identity_id="kid-miles", display_name="Miles")
    registry.identities["miles"] = {
        "id": "kid-miles", "state": "active",
        "traits": {"display_name": "Miles", "social": True},
    }
    account = ManagedAccount(
        account_id="00000000-0000-0000-0000-0000000000a1",
        person_id="miles", platform="signal",
        identifiers=frozenset(
            {Identifier("signal", "phone", "+85200000001"),
             Identifier("signal", "uuid", str(uuid.uuid4()))}),
        state=AccountState.BOUND, label="personal")
    store.accounts[account.account_id] = account
    for ident in account.identifiers:
        store.owners[ident] = account.account_id
    store.memberships[("miles", ROOM_A)] = ManagedMembership(
        person_id="miles", scope=ROOM_A, state=MembershipState.MEMBER,
        source="registration", sources=frozenset({"registration"}))
    backend.tuples.add(("Room", ROOM_A.id, "member", "miles"))
    return store, backend, registry


def _services(store, backend, registry, policy_path):
    @asynccontextmanager
    async def factory():
        topology = FileTopologySource(load_policy(policy_path))
        services = ManagedServices(
            store, backend, topology, registry, FakeLinkRequests())
        yield ManagedExport(
            bindings=store, backend=backend, topology=topology,
            access=services.access,
            probe=_Probe(store, registry))
    return factory


async def test_plan_prints_the_digest_and_counts(sources, capsys):
    store, backend, registry = _seeded()
    factory = _services(store, backend, registry, sources)
    rc = await run([
        "plan", "--policy", str(sources),
        "--database-url", "generated", "--kratos-admin-url", "generated",
        "--keto-read-url", "generated",
    ], services=factory)
    assert rc == COMPLETE
    out = capsys.readouterr().out
    assert "export:" in out and "people=1" in out and "accounts=1" in out


async def test_plan_writes_nothing(sources):
    store, backend, registry = _seeded()
    factory = _services(store, backend, registry, sources)
    rc = await run([
        "plan", "--policy", str(sources),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=factory)
    assert rc == COMPLETE
    assert list(sources.parent.iterdir()) == [sources]


async def test_plan_refuses_without_target_flags(sources, capsys):
    rc = await run([
        "plan", "--policy", str(sources), "--database-url", "generated",
    ], services=_services(*_seeded(), sources))
    assert rc == REFUSED
    assert "managed target needs" in capsys.readouterr().out


async def test_export_publishes_the_bundle_into_a_fresh_directory(
    sources, tmp_path, capsys,
):
    store, backend, registry = _seeded()
    factory = _services(store, backend, registry, sources)
    output = tmp_path / "bundle"
    rc = await run([
        "export", "--policy", str(sources), "--output", str(output),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=factory)
    assert rc == COMPLETE
    out = capsys.readouterr().out
    assert "export: complete" in out
    assert str(output) not in out
    assert sorted(p.name for p in output.iterdir()) == [
        "export-manifest.json", "identity.toml", "policy.toml",
    ]
    persons = load_persons(output / "identity.toml")
    assert persons.account_for("signal", "+85200000001").person_id == "miles"
    generated = load_policy(output / "policy.toml")
    assert generated.conversation("signal", "generated-room-a").persons == \
        frozenset({"miles"})


async def test_export_refuses_an_occupied_directory(sources, tmp_path, capsys):
    store, backend, registry = _seeded()
    factory = _services(store, backend, registry, sources)
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "existing.txt").write_text("operator file")
    rc = await run([
        "export", "--policy", str(sources), "--output", str(output),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=factory)
    assert rc == REFUSED
    assert "not fresh" in capsys.readouterr().out
    assert (output / "existing.txt").read_text() == "operator file"


async def test_export_refuses_an_empty_existing_directory(
    sources, tmp_path, capsys,
):
    """Fresh means absent — an EMPTY pre-existing directory is operator
    content too, never written into."""
    output = tmp_path / "bundle"
    output.mkdir()
    rc = await run([
        "export", "--policy", str(sources), "--output", str(output),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=_services(*_seeded(), sources))
    assert rc == REFUSED
    assert "not fresh" in capsys.readouterr().out
    assert list(output.iterdir()) == []


async def test_export_refuses_a_link_at_the_destination(
    sources, tmp_path, capsys,
):
    """A symlink at the output path is refused and its target untouched —
    the bundle never writes through another operator's directory."""
    victim = tmp_path / "victim"
    victim.mkdir()
    output = tmp_path / "bundle"
    output.symlink_to(victim, target_is_directory=True)
    rc = await run([
        "export", "--policy", str(sources), "--output", str(output),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=_services(*_seeded(), sources))
    assert rc == REFUSED
    assert "not fresh" in capsys.readouterr().out
    assert output.is_symlink()
    assert list(victim.iterdir()) == []


async def test_export_publishes_private_modes_under_an_ordinary_umask(
    sources, tmp_path,
):
    """umask 022 must not leak the bundle: the directory lands 0700 and
    every file 0600 — restrictive from creation, not chmod after."""
    import stat

    output = tmp_path / "bundle"
    old = os.umask(0o022)
    try:
        rc = await run([
            "export", "--policy", str(sources), "--output", str(output),
            "--database-url", "x", "--kratos-admin-url", "x",
            "--keto-read-url", "x",
        ], services=_services(*_seeded(), sources))
    finally:
        os.umask(old)
    assert rc == COMPLETE
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in output.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path


@pytest.mark.parametrize("failure", ["open", "write", "close"])
async def test_export_removes_its_own_partial_bundle_on_a_write_failure(
    sources, tmp_path, capsys, monkeypatch, failure,
):
    """A mid-write failure leaves nothing that looks complete: only the
    files THIS invocation staged are removed, then the fresh directory."""
    import scripts.managed_identity_export as cli

    real_open = os.open
    real_fdopen = os.fdopen
    output = tmp_path / "bundle"
    policy_fd = None

    def flaky_open(path, flags, mode=0o777, **kwargs):
        nonlocal policy_fd
        if (flags & os.O_CREAT and Path(path).parent == output
                and Path(path).name == "policy.toml"):
            if failure == "open":
                raise OSError("generated disk-full")
            policy_fd = real_open(path, flags, mode, **kwargs)
            return policy_fd
        return real_open(path, flags, mode, **kwargs)

    class FailingWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def write(self, text):
            self.handle.write(text)
            if failure == "write":
                raise OSError("generated write failure")

        def __exit__(self, *args):
            self.handle.close()
            if failure == "close":
                raise OSError("generated flush failure")

    def flaky_fdopen(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)
        return FailingWriter(handle) if fd == policy_fd else handle

    monkeypatch.setattr(cli.os, "open", flaky_open)
    monkeypatch.setattr(cli.os, "fdopen", flaky_fdopen)
    rc = await run([
        "export", "--policy", str(sources), "--output", str(output),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=_services(*_seeded(), sources))
    assert rc == RETRYABLE
    assert "unavailable: destination-write" in capsys.readouterr().out
    assert not output.exists()


async def test_refused_output_never_names_a_person_or_room(
    sources, capsys,
):
    """Diagnostics are bounded: refusal codes and counts only — the person
    id inside the private detail stays off stdout."""
    store, backend, registry = _seeded()
    store.people["miles"] = ManagedPerson(
        person_id="miles", lifecycle=PersonLifecycle.ACTIVE,
        kratos_identity_id=None, display_name="Miles")
    rc = await run([
        "plan", "--policy", str(sources),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=_services(store, backend, registry, sources))
    assert rc == REFUSED
    out = capsys.readouterr().out
    assert "untrustworthy:uncorrelated-person" in out
    assert "miles" not in out
    assert "generated-room-a" not in out
    assert "+852" not in out


async def test_export_reports_unavailable_when_a_store_is_down(
    sources, tmp_path,
):
    store, backend, registry = _seeded()
    registry.down = True
    factory = _services(store, backend, registry, sources)
    rc = await run([
        "export", "--policy", str(sources), "--output", str(tmp_path / "b"),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=factory)
    assert rc == RETRYABLE
    assert not (tmp_path / "b").exists()


async def test_plan_refuses_an_unsupported_topology(sources, capsys):
    sources.write_text(POLICY + '\n[dm]\npersons = ["miles"]\n')
    rc = await run([
        "plan", "--policy", str(sources),
        "--database-url", "x", "--kratos-admin-url", "x",
        "--keto-read-url", "x",
    ], services=_services(*_seeded(), sources))
    assert rc == REFUSED
    assert "unsupported-input:dm-admission" in capsys.readouterr().out


def test_plan_subprocess_refuses_an_invalid_policy(tmp_path):
    """The installed entry point itself, no service seam: a malformed
    policy file is a refusal, never a traceback."""
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text('default = "deny"\n[[conversations]]\n')
    root = Path(__file__).parents[2]
    result = subprocess.run(  # noqa: S603 - this interpreter, this repo's script
        [sys.executable, "scripts/managed_identity_export.py",
         "plan", "--policy", str(policy_path)],
        capture_output=True, text=True, cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert result.returncode == REFUSED
    assert "refused" in result.stdout


async def test_manifest_close_and_cleanup_failure_never_publish_a_completeness_mark(
    sources, tmp_path, capsys, monkeypatch,
):
    output = tmp_path / 'private-person-room'
    real_open, real_fdopen, real_unlink = os.open, os.fdopen, Path.unlink
    pending_fd = None

    def track_open(path, flags, mode=0o777, **kwargs):
        nonlocal pending_fd
        fd = real_open(path, flags, mode, **kwargs)
        if Path(path).parent == output and 'manifest' in Path(path).name:
            pending_fd = fd
        return fd

    class FailedClose:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self.handle

        def __exit__(self, *args):
            self.handle.close()
            raise OSError('generated close failure')

    def fail_close(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)
        return FailedClose(handle) if fd == pending_fd else handle

    def fail_unlink(path, *args, **kwargs):
        if path.parent == output:
            raise PermissionError('generated cleanup failure')
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, 'open', track_open)
    monkeypatch.setattr(os, 'fdopen', fail_close)
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    rc = await run([
        'export', '--policy', str(sources), '--output', str(output),
        '--database-url', 'x', '--kratos-admin-url', 'x', '--keto-read-url', 'x',
    ], services=_services(*_seeded(), sources))
    assert rc == RETRYABLE
    assert (output / 'identity.toml').exists()
    assert (output / 'policy.toml').exists()
    assert not (output / 'export-manifest.json').exists()
    out = capsys.readouterr().out
    assert 'destination-cleanup:PermissionError' in out
    assert 'export: complete' not in out and str(output) not in out


async def test_publication_keeps_the_gate_and_live_services_until_the_mark_exists(
    sources, tmp_path, capsys, monkeypatch,
):
    store, backend, registry = _seeded()
    output = tmp_path / 'private-person-room'
    closed = False
    published = []
    real_link = os.link

    @asynccontextmanager
    async def factory():
        nonlocal closed
        async with _services(store, backend, registry, sources)() as export:
            yield export
            assert (output / 'export-manifest.json').exists()
            closed = True

    def observe_link(source, target, *args, **kwargs):
        assert store._mutation_lock.locked(), 'publication released the identity gate'
        assert not closed
        real_link(source, target, *args, **kwargs)
        published.append(target)

    monkeypatch.setattr(os, 'link', observe_link)
    rc = await run([
        'export', '--policy', str(sources), '--output', str(output),
        '--database-url', 'x', '--kratos-admin-url', 'x', '--keto-read-url', 'x',
    ], services=factory)
    assert rc == COMPLETE and closed
    assert published == [output / 'export-manifest.json']
    assert not store._mutation_lock.locked()
    out = capsys.readouterr().out
    assert 'export: complete' in out and str(output) not in out


async def test_topology_changed_during_staging_refuses_the_publication_mark(
    sources, tmp_path, capsys, monkeypatch,
):
    output = tmp_path / 'bundle'
    real_open, real_fdopen = os.open, os.fdopen
    pending_fd = None

    def track_open(path, flags, mode=0o777, **kwargs):
        nonlocal pending_fd
        fd = real_open(path, flags, mode, **kwargs)
        if Path(path).parent == output and 'manifest' in Path(path).name:
            pending_fd = fd
        return fd

    class ChangedSource:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self.handle

        def __exit__(self, *args):
            self.handle.close()
            sources.write_text(POLICY + '\n# generated topology edit\n')

    def change_after_close(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)
        return ChangedSource(handle) if fd == pending_fd else handle

    monkeypatch.setattr(os, 'open', track_open)
    monkeypatch.setattr(os, 'fdopen', change_after_close)
    rc = await run([
        'export', '--policy', str(sources), '--output', str(output),
        '--database-url', 'x', '--kratos-admin-url', 'x', '--keto-read-url', 'x',
    ], services=_services(*_seeded(), sources))
    assert rc == REFUSED
    assert not output.exists()
    assert 'untrustworthy:source-changed' in capsys.readouterr().out
