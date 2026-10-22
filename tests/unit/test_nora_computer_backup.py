"""`nora-computer backup`, `restore` and the backup `destroy` takes first.

A recreate deletes /workspace, so the VM's disk is backed up nightly to the host
and `destroy` refuses to run without a backup. Driven through the stub host with
a directory standing in for the VM (`tests/unit/nora_computer_harness.py`): a
real `tar`, a real `sha256`, real files, and stubs only where a VM is needed
(users, ownership, `sbx`).

What this cannot show is a real sbx: `sbx exec` carrying a tar and `sbx cp`
moving it with the same digest on both sides is the README's read-back on norty,
UNVERIFIED until run.
"""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.unit.nora_computer_harness import ROOT, SCRIPT, Host

ROOM_A = "a" * 32
ROOM_B = "b" * 32


@pytest.fixture
def host(tmp_path: Path) -> Host:
    h = Host(tmp_path)
    h.make_room(ROOM_A, {
        "projects/report/data.csv": "x,y\n1,2\n",
        "projects/report/browser/notes.txt": "a PROJECT called browser: kept",
        "inbox/a.txt": "hello",
        "browser/Default/Cookies": "SECRET-COOKIES",
    })
    h.make_room(ROOM_B, {"projects/other/main.py": "print(1)\n"})
    (h.vm / "workspace" / ".tmux").mkdir()
    (h.vm / "workspace" / ".tmux" / "socket").write_text("runtime")
    return h


def _names(archive: Path) -> set[str]:
    with tarfile.open(archive) as tar:
        return {m.name.removeprefix("./") for m in tar.getmembers() if m.isfile()}


def _copied_in(host: Host) -> list[str]:
    """`sbx cp` calls that copy an archive INTO the VM (a restore's first act);
    the backup's own cp is the other direction."""
    return [c for c in host.sbx_calls("cp")
            if c.endswith("/archive.tar.gz") and "nora-computer:/tmp/nora-restore." in c]


def _backup(host: Host) -> Path:
    result = host.run("backup")
    assert result.returncode == 0, result.stdout + result.stderr
    return Path(result.stdout.strip().splitlines()[-1])


# -- backup -----------------------------------------------------------------


def test_a_backup_holds_the_rooms_files_and_leaves_the_cookies_out(host: Host) -> None:
    archive = _backup(host)
    names = _names(archive)
    assert f"{ROOM_A}/projects/report/data.csv" in names
    assert f"{ROOM_A}/inbox/a.txt" in names
    assert f"{ROOM_B}/projects/other/main.py" in names
    # A Chromium profile is a credential store: never backed up.
    assert not any("/browser/Default" in n for n in names), names
    # But a PROJECT directory that happens to be called `browser` is the room's
    # own work, and a wildcard exclude would have eaten it.
    assert f"{ROOM_A}/projects/report/browser/notes.txt" in names
    # computerd's tmux sockets are runtime, not data.
    assert not any(n.startswith(".tmux") for n in names)


def test_a_backup_is_checksummed_private_and_leaves_nothing_in_the_vm(host: Host) -> None:
    archive = _backup(host)
    sidecar = Path(f"{archive}.sha256")
    assert sidecar.exists()
    want = sidecar.read_text().split()[0]
    import hashlib

    assert hashlib.sha256(archive.read_bytes()).hexdigest() == want
    assert (host.backup_dir.stat().st_mode & 0o777) == 0o700
    assert host.vm_tmp_files() == []
    assert not list(host.backup_dir.glob("*.partial"))


def test_a_backup_is_an_audit_event_with_its_digest(host: Host) -> None:
    archive = _backup(host)
    (event,) = [e for e in host.events() if e["event"] == "backup"]
    assert event["status"] == "ok"
    assert event["detail"]["path"] == str(archive)
    assert event["detail"]["sha256"] == Path(f"{archive}.sha256").read_text().split()[0]


def test_a_copy_that_does_not_match_is_deleted_and_fails_loudly(host: Host) -> None:
    result = host.run("backup", STUB_CP_CORRUPTS="1")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "checksum mismatch" in result.stderr
    assert host.archives() == [] and not list(host.backup_dir.glob("*.partial"))
    (event,) = [e for e in host.events() if e["event"] == "backup"]
    assert event["status"] == "failed"


def test_a_tar_that_fails_in_the_vm_leaves_no_archive(host: Host) -> None:
    result = host.run("backup", STUB_VM_TAR_FAILS="1")
    assert result.returncode == 2, result.stdout + result.stderr
    assert host.archives() == []


def test_an_archive_that_is_not_a_tarball_is_refused_even_when_its_digest_matches(
    host: Host,
) -> None:
    """The digest proves the copy equals what the VM made, not that what the VM
    made is an archive. Reading it back is the second check."""
    result = host.run("backup", STUB_VM_TAR_GARBAGE="1")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "does not read back" in result.stderr
    assert host.archives() == [] and not list(host.backup_dir.glob("*.partial"))


def test_a_failed_backup_never_removes_an_earlier_one(host: Host) -> None:
    good = _backup(host)
    host.run("backup", STUB_CP_CORRUPTS="1")
    assert good.exists() and Path(f"{good}.sha256").exists()


def test_a_stopped_vm_is_skipped_not_started(host: Host) -> None:
    """`sbx exec` starts a stopped sandbox, so the nightly timer would undo a
    deliberate stop. A stopped disk is unchanged since the last backup."""
    (host.dir / "sandbox").write_text("stopped")
    result = host.run("backup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not running; skipped" in result.stderr
    assert host.sbx_calls("exec") == [] and host.archives() == []
    assert [e for e in host.events() if e["event"] == "backup"] == []   # not a failure


def test_no_sandbox_is_a_failure_not_an_empty_archive(tmp_path: Path) -> None:
    gone = Host(tmp_path, sandbox=False)
    result = gone.run("backup")
    assert result.returncode == 4, result.stdout + result.stderr
    assert gone.archives() == []


# -- retention --------------------------------------------------------------


def _age(archive: Path, seconds_ago: int) -> None:
    import time

    stamp = time.time() - seconds_ago
    os.utime(archive, (stamp, stamp))


def test_retention_keeps_the_newest_n_with_their_checksums(host: Host) -> None:
    made = []
    for age in (400, 300, 200, 100):                 # oldest first
        archive = _backup(host)
        _age(archive, age)
        made.append(archive)
    # Each backup ran its own prune against the mtimes then; one more, with KEEP=2.
    final = host.run("backup", NORA_COMPUTER_BACKUP_KEEP="2")
    assert final.returncode == 0, final.stdout + final.stderr
    left = host.archives()
    assert len(left) == 2
    assert made[-1] in left                           # the newest earlier one survived
    for archive in left:
        assert Path(f"{archive}.sha256").exists()
    assert len(list(host.backup_dir.glob("*.sha256"))) == 2


@pytest.mark.parametrize("keep", ["0", "abc", "", "-1"])
def test_a_keep_that_is_not_a_positive_number_prunes_nothing(host: Host, keep: str) -> None:
    for age in (300, 200, 100):
        _age(_backup(host), age)
    host.run("backup", NORA_COMPUTER_BACKUP_KEEP=keep)
    assert len(host.archives()) == 4


# -- restore ----------------------------------------------------------------


def _wipe(host: Host) -> None:
    import shutil

    shutil.rmtree(host.vm / "workspace")
    (host.vm / "workspace").mkdir()


def test_a_restore_after_a_recreate_puts_every_room_back_byte_for_byte(host: Host) -> None:
    archive = _backup(host)
    before = {p.relative_to(host.vm / "workspace"): p.read_bytes()
              for p in (host.vm / "workspace").rglob("*")
              if p.is_file() and "/browser/Default" not in str(p) and ".tmux" not in str(p)
              and p.name != ".restore.lock"}
    _wipe(host)                                       # what a recreate does
    result = host.run("restore", str(archive))
    assert result.returncode == 0, result.stdout + result.stderr
    after = {p.relative_to(host.vm / "workspace"): p.read_bytes()
             for p in (host.vm / "workspace").rglob("*")
             if p.is_file() and p.name != ".restore.lock"}
    assert after == before
    assert host.vm_tmp_files() == []
    assert [e["status"] for e in host.events() if e["event"] == "restore"] == ["ok"]


def test_a_restore_rebuilds_ownership_the_way_computerd_makes_users(host: Host) -> None:
    """The per-room users do not exist in a new VM and computerd creates them on
    first use with whatever uid is free, so uids in the archive mean nothing.
    Each room's directory is handed to `c-<first 12 hex>`, created the way
    computerd creates it."""
    archive = _backup(host)
    _wipe(host)
    host.run("restore", str(archive))
    calls = host.vm_user_calls()
    for scope in (ROOM_A, ROOM_B):
        user = f"c-{scope[:12]}"
        assert any(c.startswith("useradd ") and c.endswith(user) for c in calls), calls
        assert f"chown -hR {user}:{user} {host.vm}/workspace/{scope}" in calls
    # Only rooms: computerd's `.tmux` and any stray directory are not scopes.
    assert not any(".tmux" in c for c in calls)


def test_the_useradd_a_restore_runs_is_the_one_computerd_runs() -> None:
    """Two writers of the same system users must agree on how they are made, or a
    restored room's user differs from the one computerd would have made."""
    computerd = (ROOT / "deploy" / "computer" / "computerd.py").read_text()
    match = re.search(r'\["useradd", ([^\]]*?), name\]', computerd, re.S)
    assert match, "computerd's useradd call was not found"
    computerd_args = re.findall(r'"([^"]+)"', match.group(1))
    script = SCRIPT.read_text()
    restored = re.search(r"useradd (--system.*?)\"\$user\"", script, re.S)
    assert restored, "the restore's useradd call was not found"
    script_args = restored.group(1).replace("\\\n", " ").split()
    assert script_args == computerd_args, (script_args, computerd_args)
    assert 'user=c-$(printf %s "$scope" | cut -c1-12)' in script
    assert 'return f"c-{scope[:12]}"' in computerd


def test_a_restore_over_existing_rooms_is_refused_unless_forced(host: Host) -> None:
    archive = _backup(host)
    refused = host.run("restore", str(archive))
    assert refused.returncode == 7, refused.stdout + refused.stderr
    assert "already holds rooms" in refused.stderr
    assert _copied_in(host) == []                     # nothing was copied in
    forced = host.run("restore", str(archive), "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr


def test_a_restore_does_not_follow_a_symlink_already_in_a_room(
    host: Host, tmp_path: Path,
) -> None:
    """The archive check refuses links the ARCHIVE carries. A link already in
    the VM is the other half: the room controls it, and a root extraction into
    /workspace would write through it. Restore stages the archive in a fresh
    tree and renames each room over the old one, so the planted link is
    replaced, not traversed."""
    import hashlib
    import io

    archive = _backup(host)
    outside = tmp_path / "outside"
    outside.mkdir()
    (host.vm / "workspace" / ROOM_A / "escape").symlink_to(outside)
    poisoned = tmp_path / "poisoned.tar.gz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(poisoned, "w:gz") as target:
        for member in source:
            target.addfile(member, source.extractfile(member))
        payload = b"owned"
        member = tarfile.TarInfo(f"{ROOM_A}/escape/pwned")
        member.size = len(payload)
        target.addfile(member, io.BytesIO(payload))
    Path(f"{poisoned}.sha256").write_text(
        f"{hashlib.sha256(poisoned.read_bytes()).hexdigest()}  {poisoned}\n",
    )

    result = host.run("restore", str(poisoned), "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (outside / "pwned").exists()
    assert (host.vm / "workspace" / ROOM_A / "escape" / "pwned").read_text() == "owned"


def test_a_restore_holds_the_exclusive_lease_while_it_swaps_a_room(host: Host) -> None:
    """computerd takes the same lock SHARED around `run`/`state`/file ops, so a
    restore that holds it exclusively cannot rename a directory out from under
    a command. This is the lock, not the absence of one: while the shimmed
    rename is paused, a non-blocking exclusive open must be refused."""
    import fcntl
    import threading
    import time

    archive = _backup(host)
    result: dict[str, subprocess.CompletedProcess[str]] = {}

    def restore() -> None:
        result["run"] = host.run(
            "restore", str(archive), "--force",
            STUB_VM_MV_MODE="block", STUB_VM_MV_SLEEP="1.0",
        )

    thread = threading.Thread(target=restore)
    thread.start()
    marker = host.dir / "vm-mv-started"
    deadline = time.monotonic() + 20
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists(), "the restore never reached the room rename"
    with (
        open(host.vm / "workspace" / ".restore.lock", "rb") as lock,
        pytest.raises(BlockingIOError),
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert result["run"].returncode == 0, result["run"].stdout + result["run"].stderr


def test_a_destination_that_reappears_is_not_nested_inside_and_the_old_room_is_kept(
    host: Host,
) -> None:
    """The old restore did `rm -rf` then plain `mv`: a writer recreating the
    destination between them made the staged room land INSIDE it. Now the
    exact-destination rename fails, the old room is not deleted, and the
    installer names the `.restore-old` directory that holds it."""
    archive = _backup(host)
    (host.vm / "workspace" / ROOM_A / "inbox" / "a.txt").write_text("changed after backup")

    result = host.run("restore", str(archive), "--force", STUB_VM_MV_MODE="recreate")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "old room data kept in" in result.stderr, result.stderr
    destination = host.vm / "workspace" / ROOM_A
    assert (destination / "raced.txt").exists(), "the writer's directory was replaced"
    assert not (destination / ROOM_A).exists(), "the staged room was nested inside the destination"
    kept = list((host.vm / "workspace").glob(".restore-old.*"))
    assert kept, "the old room data was not kept"
    assert any(
        (backup / ROOM_A / "inbox" / "a.txt").read_text() == "changed after backup"
        for backup in kept
    )


def test_a_failed_room_move_puts_the_old_room_back_instead_of_deleting_it(host: Host) -> None:
    """The old restore deleted the room before the move, so a failed move lost
    it. The replacement now lands first; on failure the old room is restored
    from the aside directory and the staging/backup directories are cleaned."""
    archive = _backup(host)
    before = (host.vm / "workspace" / ROOM_A / "inbox" / "a.txt").read_text()

    result = host.run("restore", str(archive), "--force", STUB_VM_MV_MODE="fail")

    assert result.returncode != 0, result.stdout + result.stderr
    assert (host.vm / "workspace" / ROOM_A / "inbox" / "a.txt").read_text() == before
    assert not [p for p in (host.vm / "workspace").glob(".restore.*") if p.is_dir()], \
        "staging directory left behind"
    assert not list((host.vm / "workspace").glob(".restore-old.*")), \
        "an empty backup was left behind"


def test_an_archive_without_its_checksum_is_not_restored(host: Host) -> None:
    archive = _backup(host)
    Path(f"{archive}.sha256").unlink()
    _wipe(host)
    result = host.run("restore", str(archive))
    assert result.returncode == 3, result.stdout + result.stderr
    assert _copied_in(host) == []


def test_an_archive_that_does_not_match_its_checksum_is_not_restored(host: Host) -> None:
    archive = _backup(host)
    archive.write_bytes(archive.read_bytes() + b"tamper")
    _wipe(host)
    result = host.run("restore", str(archive))
    assert result.returncode == 3, result.stdout + result.stderr
    assert "does not match its checksum" in result.stderr
    assert _copied_in(host) == []


def test_restore_wants_an_archive(host: Host, tmp_path: Path) -> None:
    assert host.run("restore").returncode == 2
    assert host.run("restore", str(tmp_path / "nope.tar.gz")).returncode == 2


def test_restore_wants_a_sandbox_to_restore_into(host: Host) -> None:
    archive = _backup(host)
    (host.dir / "sandbox").unlink()
    result = host.run("restore", str(archive))
    assert result.returncode == 6, result.stdout + result.stderr
    assert _copied_in(host) == []


# -- destroy ----------------------------------------------------------------


def test_destroy_takes_a_backup_first_and_only_then_removes_the_vm(host: Host) -> None:
    result = host.run("destroy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(host.destroy_archives()) == 1 and host.archives() == []
    calls = host.sbx_calls()
    cp = next(i for i, c in enumerate(calls) if c.startswith("cp "))
    rm = next(i for i, c in enumerate(calls) if c.startswith("rm "))
    assert cp < rm, "the VM was removed before its files were copied out"
    assert not host.sandbox_exists()
    (event,) = [e for e in host.events() if e["event"] == "destroy"]
    assert event["detail"]["no_backup_flag"] == "0"
    assert event["detail"]["backup"] == str(host.destroy_archives()[0])


def test_destroy_refuses_when_the_backup_fails_and_removes_nothing(host: Host) -> None:
    result = host.run("destroy", STUB_CP_CORRUPTS="1")
    assert result.returncode == 9, result.stdout + result.stderr
    assert "destroy REFUSED" in result.stderr
    assert host.sandbox_exists()
    assert host.sbx_calls("rm") == []
    (event,) = [e for e in host.events() if e["event"] == "destroy"]
    assert event["status"] == "failed" and event["detail"]["reason"] == "backup_failed"


def test_destroy_with_no_backup_says_so_and_deletes(host: Host) -> None:
    result = host.run("destroy", "--no-backup", STUB_CP_CORRUPTS="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert host.archives() == []
    assert not host.sandbox_exists()
    (event,) = [e for e in host.events() if e["event"] == "destroy"]
    assert event["detail"]["no_backup_flag"] == "1" and event["detail"]["backup"] == "none"


def test_destroy_of_a_stopped_vm_still_backs_it_up_first(host: Host) -> None:
    """Unlike the nightly timer, destroy MAY start a stopped VM: the files are
    about to be deleted and must be had first."""
    (host.dir / "sandbox").write_text("stopped")
    result = host.run("destroy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(host.destroy_archives()) == 1


def test_destroy_does_not_take_a_flag_it_does_not_know(host: Host) -> None:
    result = host.run("destroy", "--force")
    assert result.returncode == 2
    assert host.sandbox_exists()


# -- the nightly job --------------------------------------------------------


def test_the_nightly_units_run_the_backup_subcommand_on_a_persistent_timer() -> None:
    service = (ROOT / "deploy" / "computer" / "nora-computer-backup.service").read_text()
    timer = (ROOT / "deploy" / "computer" / "nora-computer-backup.timer").read_text()
    assert "Type=oneshot" in service
    assert re.search(r"^ExecStart=%h/workspace/nora/deploy/computer/nora-computer backup$",
                     service, re.M)
    assert re.search(r"^OnCalendar=\*-\*-\* \d\d:\d\d:\d\d$", timer, re.M)
    assert "Persistent=true" in timer
    assert "Unit=nora-computer-backup.service" in timer
    readme = (ROOT / "deploy" / "computer" / "README.md").read_text()
    assert "systemctl --user enable --now nora-computer-backup.timer" in readme


# -- the archive never sits in a shared /tmp --------------------------------


def test_the_archive_is_made_in_a_private_directory_under_a_077_umask(host: Host) -> None:
    """The archive holds every room's files. Written straight into /tmp under
    root's usual umask of 022 it would be a world-readable file in a
    world-listable directory for as long as it lived: it is made in a
    `mktemp -d` directory (0700, root's) under umask 077."""
    result = host.run("backup", STUB_VM_TAR_RECORD="1")
    assert result.returncode == 0, result.stdout + result.stderr
    log = host.vm_tar_log()
    assert "umask 0077" in log, log
    (directory,) = [ln for ln in log if ln.startswith("dir ")]
    assert directory.split()[1] == "drwx------", directory
    assert re.fullmatch(r"dir drwx------ /tmp/nora-backup\.[A-Za-z0-9]{6}", directory), directory
    assert "archive -rw-------" in log, log
    # Nothing is directly in /tmp, and the whole directory is gone afterwards.
    assert host.vm_tmp_files() == []


def test_a_directory_that_cannot_be_removed_fails_the_backup_and_says_where(host: Host) -> None:
    """Not `|| true`: a leftover archive of every room's files is the exposure,
    so a failed cleanup is a failed backup, the copy on the host is discarded,
    and the log names the directory to remove by hand."""
    result = host.run("backup", STUB_VM_RM_FAILS="1")
    assert result.returncode == 5, result.stdout + result.stderr
    assert "could NOT remove /tmp/nora-backup." in result.stderr
    assert host.archives() == [] and not list(host.backup_dir.glob("*.partial"))
    (event,) = [e for e in host.events() if e["event"] == "backup"]
    assert event["status"] == "failed"


def test_a_remove_that_reports_success_but_left_the_directory_is_still_a_failure(
    host: Host,
) -> None:
    """The removal is checked, not trusted: `rm` exiting 0 is a claim, and the
    directory being gone is the fact."""
    result = host.run("backup", STUB_VM_RM_LIES="1")
    assert result.returncode == 5, result.stdout + result.stderr
    assert host.archives() == []


def test_a_vm_that_answers_something_else_for_mktemp_is_refused(host: Host) -> None:
    sbx = host.dir.parent / "bin" / "sbx"
    before = 'exec)\n      exec "$STUB_DIR/bin-vm-exec" "$@" ;;'
    after = ('exec)\n      [ "$5" = mktemp ] && { echo /tmp; exit 0; }\n'
             '      exec "$STUB_DIR/bin-vm-exec" "$@" ;;')
    text = sbx.read_text().replace(before, after)
    assert text != sbx.read_text()
    sbx.write_text(text)
    result = host.run("backup")
    assert result.returncode == 2, result.stdout + result.stderr
    assert host.archives() == []


# -- retention against an empty VM ------------------------------------------


def _wipe_rooms(host: Host) -> None:
    import shutil

    shutil.rmtree(host.vm / "workspace")
    (host.vm / "workspace").mkdir()


def test_an_empty_workspace_is_not_backed_up_and_prunes_nothing(host: Host) -> None:
    """A recreated VM has an empty /workspace. Backed up, that archive would
    verify, count toward the window, and after KEEP nights push out the last
    backup that holds any room."""
    for age in (300, 200):
        _age(_backup(host), age)
    keep = {p.name for p in host.archives()}
    assert len(keep) == 2
    _wipe_rooms(host)
    for _ in range(6):
        result = host.run("backup", NORA_COMPUTER_BACKUP_KEEP="2")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "holds no rooms; nothing to back up" in result.stderr
    assert {p.name for p in host.archives()} == keep
    assert [e for e in host.events() if e["event"] == "backup"][-1]["status"] == "ok"
    # the skips are no events
    assert len([e for e in host.events() if e["event"] == "backup"]) == 2


def test_a_room_less_vm_with_only_hidden_entries_is_still_empty(host: Host) -> None:
    _wipe_rooms(host)
    (host.vm / "workspace" / ".tmux").mkdir()
    (host.vm / "workspace" / "lost+found").mkdir()
    result = host.run("backup")
    assert result.returncode == 0 and host.archives() == []


def test_the_backup_a_destroy_takes_is_outside_the_rotation_and_never_pruned(host: Host) -> None:
    host.run("destroy")
    (kept,) = host.destroy_archives()
    (host.dir / "sandbox").touch()
    for age in (500, 400, 300):
        h = host.run("backup", NORA_COMPUTER_BACKUP_KEEP="1")
        assert h.returncode == 0, h.stdout + h.stderr
        _age(Path(h.stdout.strip().splitlines()[-1]), age)
    assert len(host.archives()) == 1                     # the rotation kept its one
    assert kept.exists() and Path(f"{kept}.sha256").exists()


def test_destroy_of_a_vm_with_no_rooms_has_nothing_to_back_up(host: Host) -> None:
    _wipe_rooms(host)
    result = host.run("destroy")
    assert result.returncode == 0, result.stdout + result.stderr
    assert host.destroy_archives() == [] and not host.sandbox_exists()
    (event,) = [e for e in host.events() if e["event"] == "destroy"]
    assert event["detail"]["backup"] == "empty"


# -- restore: private directory, digest inside the VM ------------------------


def test_a_restore_copies_into_a_private_directory_and_leaves_none_behind(host: Host) -> None:
    archive = _backup(host)
    _wipe_rooms(host)
    assert host.run("restore", str(archive)).returncode == 0
    (copy,) = _copied_in(host)
    pattern = r"nora-computer:/tmp/nora-restore\.[A-Za-z0-9]{6}/archive\.tar\.gz$"
    assert re.search(pattern, copy), copy
    assert host.vm_tmp_files() == []


def test_the_digest_is_checked_inside_the_vm_before_anything_is_extracted(host: Host) -> None:
    """The copy into the VM changed on the way (or was swapped there): the VM's
    own sha256 of the file tar will read differs, and nothing is extracted."""
    archive = _backup(host)
    _wipe_rooms(host)
    result = host.run("restore", str(archive), STUB_CP_IN_CORRUPTS="1")
    assert result.returncode == 4, result.stdout + result.stderr
    assert not any((host.vm / "workspace").iterdir())
    assert host.vm_tmp_files() == []
    assert [e["status"] for e in host.events() if e["event"] == "restore"] == ["failed"]


def test_a_restore_whose_directory_cannot_be_removed_says_so(host: Host) -> None:
    archive = _backup(host)
    _wipe_rooms(host)
    result = host.run("restore", str(archive), STUB_VM_RM_FAILS="1")
    assert result.returncode == 5, result.stdout + result.stderr
    assert "could NOT be removed" in result.stderr


def _hostile(path: Path, build) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        build(tar)
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(f"{path}.sha256").write_text(f"{digest}  {path.name}\n")
    return path


def _member(tar: tarfile.TarFile, name: str, data: bytes = b"x", **kw) -> None:
    import io

    info = tarfile.TarInfo(name)
    info.size = len(data)
    for key, value in kw.items():
        setattr(info, key, value)
    tar.addfile(info, io.BytesIO(data) if info.isreg() else None)


def _link(tar: tarfile.TarFile, name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    tar.addfile(info)


HOSTILE = {
    "a parent-directory member": lambda t: _member(t, f"{ROOM_A}/../../x"),
    "an absolute path": lambda t: _member(t, "/etc/cron.d/x"),
    "a top level that is not a room": lambda t: _member(t, "etc/passwd"),
    "a symlink to /etc": lambda t: _link(t, f"{ROOM_A}/etc", "/etc"),
    "a symlink that climbs out": lambda t: _link(t, f"{ROOM_A}/up", "../../x"),
    "a symlink into another room": lambda t: _link(t, f"{ROOM_A}/other", f"../{ROOM_B}"),
    "a hard link to /etc/passwd": lambda t: _link(t, f"{ROOM_A}/p", "/etc/passwd", tarfile.LNKTYPE),
    "a device node": lambda t: _member(t, f"{ROOM_A}/null", b"", type=tarfile.CHRTYPE),
    "a fifo": lambda t: _member(t, f"{ROOM_A}/pipe", b"", type=tarfile.FIFOTYPE),
}


@pytest.mark.parametrize("what", sorted(HOSTILE))
def test_a_hostile_archive_is_refused_before_a_byte_reaches_the_vm(
    host: Host, tmp_path: Path, what: str,
) -> None:
    archive = _hostile(tmp_path / "hostile.tar.gz", HOSTILE[what])
    _wipe_rooms(host)
    result = host.run("restore", str(archive))
    assert result.returncode == 3, (what, result.stdout, result.stderr)
    assert "could write outside /workspace" in result.stderr
    assert _copied_in(host) == []
    assert not any((host.vm / "workspace").iterdir())
    assert not (host.vm / "x").exists() and not (host.vm / "etc").exists()


def test_a_write_through_a_symlink_that_is_in_the_archive_is_refused(
    host: Host, tmp_path: Path,
) -> None:
    def build(tar):
        _link(tar, f"{ROOM_A}/d", "sub")
        _member(tar, f"{ROOM_A}/d/file")

    archive = _hostile(tmp_path / "through.tar.gz", build)
    result = host.run("restore", str(archive))
    assert result.returncode == 3, result.stdout + result.stderr
    assert "written through the symlink" in result.stderr


def test_a_real_backup_passes_the_archive_check(host: Host) -> None:
    """The positive control: the check refuses hostile archives and not the
    archives this script makes."""
    archive = _backup(host)
    check = subprocess.run(  # noqa: S603
        ["python3", str(ROOT / "deploy" / "computer" / "archive_check.py"), str(archive)],
        capture_output=True, text=True, check=False,
    )
    assert check.returncode == 0, check.stdout
