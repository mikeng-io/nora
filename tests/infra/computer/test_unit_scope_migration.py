"""Offline migration proof on synthetic disks; no norty or live data."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from nora.domain.computer import computer_scope, computer_unit_scope
from tests.contract.test_sandbox_runtime_contract import load_computerd

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def migration(monkeypatch):
    monkeypatch.setitem(sys.modules, "computerd", load_computerd())
    spec = importlib.util.spec_from_file_location(
        "unit_migration", ROOT / "deploy/computer/migrate_unit_scope.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def disk(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    a, b = computer_scope("signal", "s"), computer_scope("discord", "d")
    unit = computer_unit_scope("team")
    for scope, content in [(a, b"signal"), (b, b"discord")]:
        room = root / scope
        room.mkdir(mode=0o700)
        (room / "report.txt").write_bytes(content)
        (room / ".hidden").write_bytes(b"secret\x00bytes")
        (room / "browser/default").mkdir(parents=True)
        (room / "browser/default/Cookies").write_bytes(content + b"cookie")
        (room / "run").write_text("#!/bin/sh\ntrue\n")
        (room / "run").chmod(0o700)
    return root, {a: unit, b: unit}, tmp_path / "journal.json"


def test_two_rooms_keep_every_byte_and_mode_and_a_verified_repeat_is_idempotent(migration, disk):
    root, mapping, journal = disk
    document = migration.plan(root, mapping)
    assert not journal.exists() and list(root.iterdir())
    receipt = migration.migrate(root, document, journal, action="apply", isolate=False)
    assert receipt["verified"] is True
    unit = next(iter(mapping.values()))
    for old in mapping:
        destination = root / unit / "rooms" / old
        assert (destination / "report.txt").read_bytes() == (root / old / "report.txt").read_bytes()
        assert (destination / ".hidden").read_bytes() == b"secret\x00bytes"
        assert (destination / "browser/default/Cookies").read_bytes().endswith(b"cookie")
        assert (destination / "run").stat().st_mode & 0o777 == 0o700
        assert (root / old).exists()  # verification precedes retirement
    assert not (root / unit / "browser").exists()  # unit profile starts fresh
    assert migration.migrate(root, document, journal, action="apply", isolate=False) == receipt
    retired = migration.migrate(root, document, journal, action="retire", isolate=False)
    assert set(retired["retired"]) == set(mapping)
    assert all(not (root / old).exists() for old in mapping)
    assert migration.migrate(root, document, journal, action="retire", isolate=False) == retired


def test_corrupt_destination_never_retires_sources(migration, disk):
    root, mapping, journal = disk
    document = migration.plan(root, mapping)
    migration.migrate(root, document, journal, action="apply", isolate=False)
    old, unit = next(iter(mapping.items()))
    (root / unit / "rooms" / old / "report.txt").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="destination changed"):
        migration.migrate(root, document, journal, action="retire", isolate=False)
    assert all((root / s / ".hidden").read_bytes() == b"secret\x00bytes" for s in mapping)


def test_interrupted_retirement_blocks_fresh_inventory_and_plan(migration, disk, monkeypatch):
    root, mapping, journal = disk
    (root / ".unit-migration-sources").mkdir(mode=0o700)
    document = migration.plan(root, mapping)  # an empty quarantine is harmless
    migration.migrate(root, document, journal, action="apply", isolate=False)
    old, unit = next(iter(mapping.items()))
    rename = os.rename

    def interrupted(*args, **kwargs):
        rename(*args, **kwargs)
        raise OSError("interrupted after rename, before journal update")

    with monkeypatch.context() as patch:
        patch.setattr(migration.os, "rename", interrupted)
        with pytest.raises(OSError, match="interrupted after rename"):
            migration.migrate(root, document, journal, action="retire", isolate=False)
    assert json.loads(journal.read_text())["retired"] == []
    assert not (root / old).exists()
    assert migration._read(root, [".unit-migration-sources", old]) == document["sources"][old]
    assert migration._read(root, [unit, "rooms", old]) == document["sources"][old]
    remaining = {s: u for s, u in mapping.items() if s != old}
    with pytest.raises(ValueError, match="quarantined sources require original plan/journal"):
        migration.inventory(root)
    with pytest.raises(ValueError, match="quarantined sources require original plan/journal"):
        migration.plan(root, remaining)
    assert all((root / s).exists() for s in remaining)


def test_retire_quarantine_unblocks_inventory_and_produces_a_restorable_backup(migration, disk):
    root, mapping, journal = disk
    document = migration.plan(root, mapping)
    migration.migrate(root, document, journal, action="apply", isolate=False)
    retired = migration.migrate(root, document, journal, action="retire", isolate=False)
    readback = {"plan_sha256": retired["plan_sha256"], "owner_readback_verified": True}
    # Full rollback archives live outside /workspace and must survive cleanup.
    backups = [journal.parent / name for name in ("workspace.tar.gz", "browser-profiles.tar.gz")]
    for backup in backups:
        backup.write_bytes(b"retained rollback archive")
    for _ in range(2):  # a verified cleanup repeat is harmless
        result = migration.migrate(
            root, document, journal, action="retire-quarantine", isolate=False,
            live_readback=readback,
        )
        assert result["verified"] is True
        assert not (root / ".unit-migration-sources").exists()
        assert migration.inventory(root) == sorted(set(mapping.values()))
    unit = next(iter(mapping.values()))
    assert unit in migration.plan(root, {unit: computer_unit_scope("new-team")})["sources"]
    for old, unit in mapping.items():
        assert migration._read(root, [unit, "rooms", old]) == document["sources"][old]
    assert all(backup.read_bytes() == b"retained rollback archive" for backup in backups)
    # Match backup_in_vm: exclude only top-level browser profiles and .tmux.
    (root / unit / "browser").mkdir()
    (root / unit / "browser/live-profile").write_bytes(b"excluded")
    (root / ".tmux").mkdir(exist_ok=True)
    (root / ".tmux/socket-placeholder").write_bytes(b"excluded")
    archive = journal.parent / "post-migration.tar.gz"
    subprocess.run(  # noqa: S603 - fixed tar argv on a synthetic disk
        ["tar", "--exclude=./.tmux", f"--exclude=./{unit}/browser",
         "-C", str(root), "-czf", str(archive), "."],
        env={**os.environ, "COPYFILE_DISABLE": "1"},  # no macOS metadata in the Linux shape
        check=True,
    )
    checked = subprocess.run(  # noqa: S603 - repo validator, synthetic archive
        [sys.executable, str(ROOT / "deploy/computer/archive_check.py"), str(archive)],
        capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("unaccounted-item", "not accounted for in journal"),
        ("incomplete-journal", "journal must account for every source"),
        ("unverified-journal", "journal must account for every source"),
        ("missing-readback", "successful live owner readback"),
        ("wrong-plan-readback", "successful live owner readback"),
        ("changed-destination", "destination changed"),
        ("changed-quarantine", "quarantined source changed"),
    ],
)
def test_retire_quarantine_refuses_without_complete_verified_accounting(
    migration, disk, failure, reason
):
    root, mapping, journal = disk
    document = migration.plan(root, mapping)
    migration.migrate(root, document, journal, action="apply", isolate=False)
    retired = migration.migrate(root, document, journal, action="retire", isolate=False)
    readback = {"plan_sha256": retired["plan_sha256"], "owner_readback_verified": True}
    quarantine = root / ".unit-migration-sources"
    old, unit = next(iter(mapping.items()))
    state = json.loads(journal.read_text())
    if failure == "unaccounted-item":
        (quarantine / ("f" * 32)).mkdir()
    elif failure == "incomplete-journal":
        state["retired"].remove(old)
    elif failure == "unverified-journal":
        state["completed"].remove(old)
    elif failure == "missing-readback":
        readback = None
    elif failure == "wrong-plan-readback":
        readback["plan_sha256"] = "another plan"
    elif failure == "changed-destination":
        (root / unit / "rooms" / old / "report.txt").write_bytes(b"changed")
    elif failure == "changed-quarantine":
        (quarantine / old / "report.txt").write_bytes(b"changed")
    journal.write_text(json.dumps(state))
    before = migration._read(root, [quarantine.name])
    with pytest.raises(ValueError, match=reason):
        migration.migrate(
            root, document, journal, action="retire-quarantine", isolate=False,
            live_readback=readback,
        )
    assert migration._read(root, [quarantine.name]) == before


def test_unmapped_interrupted_and_conflicting_moves_preserve_sources(migration, disk):
    root, mapping, journal = disk
    old, unit = next(iter(mapping.items()))
    with pytest.raises(ValueError, match="unmapped"):
        migration.plan(root, {old: unit})
    document = migration.plan(root, mapping)
    target = root / unit / "rooms" / old
    target.mkdir(parents=True)
    (target / "report.txt").write_bytes(b"pre-existing")
    with pytest.raises(ValueError, match="conflicting or interrupted"):
        migration.migrate(root, document, journal, action="apply", isolate=False)
    assert (target / "report.txt").read_bytes() == b"pre-existing"
    assert all((root / s).exists() for s in mapping)


def test_symlinks_are_preserved_without_copying_or_chowning_their_targets(
    migration, disk, tmp_path
):
    root, mapping, journal = disk
    outside = tmp_path / "outside"
    outside.write_bytes(b"other unit")
    before = outside.stat()
    old, unit = next(iter(mapping.items()))
    (root / old / "link").symlink_to(outside)
    (root / old / "directory-link").symlink_to(tmp_path)
    document = migration.plan(root, mapping)
    migration.migrate(root, document, journal, action="apply", isolate=False)
    assert os.readlink(root / unit / "rooms" / old / "link") == str(outside)
    assert outside.read_bytes() == b"other unit" and outside.stat().st_uid == before.st_uid
    assert (root / unit / "rooms" / old / "directory-link").is_symlink()


def test_changed_source_cannot_be_retired_from_a_stale_plan(migration, disk):
    root, mapping, journal = disk
    document = migration.plan(root, mapping)
    old = next(iter(mapping))
    (root / old / "report.txt").write_bytes(b"new work after plan")
    with pytest.raises(ValueError, match="source changed"):
        migration.migrate(root, document, journal, action="apply", isolate=False)
    assert not journal.exists() and all((root / s).exists() for s in mapping)


def test_real_linux_users_can_resume_migrated_files_but_other_units_and_old_users_cannot():
    """Run actual chown/setuid/raw commands on a synthetic Linux disk."""
    import aiohttp
    from testcontainers.core.container import DockerContainer

    code = r"""
import asyncio, hashlib, json, os, pwd, stat
from pathlib import Path
import sys
sys.path.insert(0, '/src/deploy/computer')
from computerd import Computer
from migrate_unit_scope import migrate, plan

def key(text): return hashlib.sha256(text.encode()).hexdigest()[:32]
root = Path('/workspace'); root.mkdir(mode=0o755)
old_a, old_b = key('signal:s'), key('discord:d')
unit, other = key('unit:team'), key('unit:private')
computer = Computer(root=root, token='test', isolate_scopes=True)
async def run(scope, command):
    return await computer.run(scope, {'command':command, 'timeout_s':5})
async def main():
    for old, content in [(old_a, 'signal'), (old_b, 'discord')]:
        result = await run(old, f'echo {content} > report.txt; mkdir -p browser/default; '
                           'echo cookie > browser/default/Cookies; echo hidden > .hidden; '
                           'chmod 600 report.txt')
        assert result['exit_code'] == 0, result
    uid = computer.owner.ids(old_a)[0]
    outside = root / 'outside'
    outside.write_text('must not copy or chown')
    before = outside.stat()
    (root / old_a / 'outside-link').symlink_to(outside)
    # Unmapped files outside a scope are rejected, so keep the target off disk.
    outside.rename('/outside')
    (root / old_a / 'outside-link').unlink()
    (root / old_a / 'outside-link').symlink_to('/outside')
    document = plan(root, {old_a:unit, old_b:unit})
    journal = Path('/migration.json')
    receipt = migrate(root, document, journal, action='apply')
    assert receipt['verified']
    assert Path('/outside').stat().st_uid == before.st_uid
    assert (root / unit).stat().st_mode & 0o777 == 0o700
    assert (root / unit).stat().st_uid == computer.owner.ids(unit)[0]
    assert not (root / unit / 'browser').exists()
    for old, content in [(old_a, 'signal'), (old_b, 'discord')]:
        read = await run(unit, f'cat rooms/{old}/report.txt')
        assert read['exit_code'] == 0 and read['stdout'].strip() == content, read
    denied = await run(other, f'cat {root}/{unit}/rooms/{old_a}/report.txt')
    assert denied['exit_code'] != 0 and 'signal' not in denied['stdout'], denied
    migrate(root, document, journal, action='retire')
    denied_old = await run(old_a, f'cat {root}/{unit}/rooms/{old_a}/report.txt')
    assert denied_old['exit_code'] != 0 and 'signal' not in denied_old['stdout'], denied_old
    assert (root / unit / 'rooms' / old_a / '.hidden').read_text() == 'hidden\n'
asyncio.run(main())
print('UNIT_ISOLATION_AND_MIGRATION_OK')
"""
    with (
        DockerContainer("python:3.12-slim")
        .with_volume_mapping(str(ROOT), "/src", mode="ro")
        .with_volume_mapping(str(Path(aiohttp.__file__).parent.parent), "/deps", mode="ro")
        .with_env("PYTHONPATH", "/deps")
        .with_command("sleep 120")
    ) as container:
        result = container.exec(["python", "-c", code])
        assert result.exit_code == 0, result.output.decode()
        assert b"UNIT_ISOLATION_AND_MIGRATION_OK" in result.output


def test_interrupted_copy_is_held_for_reconciliation_without_retiring_any_source(
    migration,
    disk,
    monkeypatch,
):
    root, mapping, journal = disk
    document = migration.plan(root, mapping)
    original = migration._copy

    def interrupted(src, dst, ids):
        original(src, dst, ids)
        raise OSError("interrupted before verification")

    with monkeypatch.context() as patch:
        patch.setattr(migration, "_copy", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            migration.migrate(root, document, journal, action="apply", isolate=False)
    assert all(migration._read(root, [old]) == document["sources"][old] for old in mapping)
    with pytest.raises(ValueError, match="conflicting or interrupted"):
        migration.migrate(root, document, journal, action="apply", isolate=False)
    assert all((root / old).exists() for old in mapping)


GATE_RUNBOOK = ROOT / "design/computer-unit-scope/01-migration.md"
NORA_COMPUTER = ROOT / "deploy/computer/nora-computer"
REAL_STATUS_LINE = "sandbox=running computerd=healthy egress=ok endpoint=http://172.17.0.1:18765"
UNHEALTHY_LINES = [
    "sandbox=running computerd=down egress=ok endpoint=x",
    "sandbox=running computerd=up egress=ok endpoint=x",
    "sandbox=running computerd=healthy egress=WRONG endpoint=x",
    "sandbox=running computerd=healthy egress=unknown endpoint=x",
]


def _gate_blocks():
    return re.findall(
        r'^case "\$status_line" in\n.*?^esac$', GATE_RUNBOOK.read_text(), flags=re.S | re.M
    )


def _run_gate(block, status_line):
    script = f"status_line={shlex.quote(status_line)}\n{block}"
    return subprocess.run(  # noqa: S603 - fixed bash argv, the case block is read from the repo runbook
        ["bash", "-c", script], capture_output=True, text=True
    )


def test_both_runbook_gates_admit_the_real_status_line_and_refuse_every_unhealthy_one():
    blocks = _gate_blocks()
    assert len(blocks) == 2
    for block in blocks:
        assert _run_gate(block, REAL_STATUS_LINE).returncode == 0
        for line in UNHEALTHY_LINES:
            result = _run_gate(block, line)
            assert result.returncode == 1, (line, result.stderr)
            assert "computerd not healthy" in result.stderr


def test_real_status_line_is_the_one_cmd_status_prints():
    source = NORA_COMPUTER.read_text()
    match = re.search(r'echo "(sandbox=[^"]*)"', source)
    assert match, "cmd_status echo format not found"
    template = match.group(1)
    assert re.findall(r"(\w+)=", template) == ["sandbox", "computerd", "egress", "endpoint"]
    assert "  " not in template
    rendered = (
        template.replace("$state", "running")
        .replace("$health", "healthy")
        .replace("$egress", "ok")
        .replace("$HOST_IP", "172.17.0.1")
        .replace("$HOST_PORT", "18765")
    )
    assert rendered == REAL_STATUS_LINE
