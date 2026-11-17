"""`scripts/git_archive.py` against real git: the tag is the record.

A throwaway repository with a bare `origin` holds branches and worktrees in
each archive class; the script runs as a subprocess (`make archive`'s
invocation). `gh` is a stub for the `git_gc.py` custody checks, and a `git`
shim that delegates to the real binary injects the failures real git will
not produce on demand — a failed `worktree list` or `status` — to prove the
steps fail closed. Every other guard is exercised with real repository
state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.infra.test_git_gc import Repo, _gc, _gh_stub, _line, _pre_push_hook

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "git_archive.py"
TAG = "refs/tags/archive/chore/wip"


def _archive(repo: Repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this repo's own script
        [sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        env=repo.env,
        capture_output=True,
        text=True,
    )


def _git_shim(bin_dir: Path, fail_on: str) -> None:
    """A `git` that delegates to the real binary but fails the named call."""
    real = shutil.which("git", path=os.environ["PATH"])
    shim = bin_dir / "git"
    shim.write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        f"  *\"{fail_on}\"*) exit 1 ;;\n"
        f"  *) exec \"{real}\" \"$@\" ;;\nesac\n"
    )
    shim.chmod(0o755)


def _tag_message(repo: Repo, name: str) -> str:
    return repo.git("tag", "-l", "--format=%(contents)", name).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    origin = tmp_path / "origin.git"
    main = tmp_path / "main"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)],  # noqa: S603
                   check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(main)],  # noqa: S603
                   check=True)
    r = Repo(main, env)
    r.git("config", "user.email", "archive@test.invalid")
    r.git("config", "user.name", "archive test")
    (main / "tracked.txt").write_text("original\n")
    r.git("add", "tracked.txt")
    r.git("commit", "-qm", "init")
    r.git("remote", "add", "origin", str(origin))
    r.git("push", "-qu", "origin", "master")

    # chore/wip carries one commit outside master, local and remote.
    r.git("checkout", "-qb", "chore/wip")
    r.git("commit", "-qm", "wip", "--allow-empty")
    r.git("push", "-qu", "origin", "chore/wip")
    r.git("checkout", "-q", "master")

    bin_dir = tmp_path / "bin"
    _gh_stub(bin_dir, "[]", "[]")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return r


def test_a_clean_branch_archives_at_its_tip(repo: Repo) -> None:
    tip = repo.head("chore/wip")
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode == 0, out.stderr
    assert repo.git("rev-parse", f"{TAG}^{{commit}}").stdout.strip() == tip
    message = _tag_message(repo, "archive/chore/wip")
    assert f"source-tip: {tip}" in message
    assert "reason: done" in message
    # the tag object on origin is the local one
    remote = repo.git("ls-remote", "origin", TAG).stdout
    assert repo.git("rev-parse", TAG).stdout.strip() in remote
    # archive never deletes the branch; gc owns retirement.
    assert "chore/wip" in repo.git("branch", "--list", "chore/wip").stdout


def test_a_dirty_worktree_is_refused_and_left_untouched(repo: Repo) -> None:
    """Staged, unstaged and untracked work is the owner's, never the tag's."""
    wt = repo.tmp / "wt"
    repo.git("worktree", "add", "-q", str(wt), "chore/wip")
    (wt / "staged.txt").write_text("staged\n")
    repo.git("-C", str(wt), "add", "staged.txt")
    (wt / "tracked.txt").write_text("unstaged change\n")
    (wt / "untracked.txt").write_text("untracked\n")

    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "commit or discard" in out.stderr
    assert repo.git("rev-parse", "--verify", TAG).returncode != 0
    assert repo.git("ls-remote", "origin", TAG).stdout.strip() == ""
    assert (wt / "staged.txt").read_text() == "staged\n"
    assert (wt / "tracked.txt").read_text() == "unstaged change\n"
    assert (wt / "untracked.txt").read_text() == "untracked\n"


def test_the_worktree_holding_the_branch_is_found_and_checked(repo: Repo) -> None:
    """The worktree holding the branch is found and checked on its own."""
    wt = repo.tmp / "wt"
    repo.git("worktree", "add", "-q", str(wt), "chore/wip")
    (wt / "scratch.txt").write_text("uncommitted\n")
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "commit or discard" in out.stderr
    assert repo.git("rev-parse", "--verify", TAG).returncode != 0
    assert (wt / "scratch.txt").exists()


def test_a_failed_worktree_listing_aborts(repo: Repo) -> None:
    """An unreadable worktree registry is an abort, never 'no worktree'."""
    wt = repo.tmp / "wt"
    repo.git("worktree", "add", "-q", str(wt), "chore/wip")
    _git_shim(repo.tmp / "bin", "worktree list")
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "worktree list" in out.stderr
    assert repo.git("rev-parse", "--verify", TAG).returncode != 0


def test_a_failed_status_check_aborts(repo: Repo) -> None:
    """A status check that cannot answer is not read as clean."""
    wt = repo.tmp / "wt"
    repo.git("worktree", "add", "-q", str(wt), "chore/wip")
    _git_shim(repo.tmp / "bin", "status --porcelain")
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "status" in out.stderr
    assert repo.git("rev-parse", "--verify", TAG).returncode != 0


def test_the_tip_is_resolved_after_the_fetch(repo: Repo) -> None:
    """A remote that advanced is archived, not the stale tracking ref."""
    other = repo.tmp / "other"
    subprocess.run(["git", "clone", "-q", str(repo.tmp / "origin.git"),  # noqa: S603
                    str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email",  # noqa: S603
                    "a@b"], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name",  # noqa: S603
                    "t"], check=True)
    subprocess.run(["git", "-C", str(other), "checkout", "-qb",  # noqa: S603
                    "chore/wip", "origin/chore/wip"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-qm", "more",  # noqa: S603
                    "--allow-empty"], check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin",  # noqa: S603
                    "chore/wip"], check=True)
    new_tip = subprocess.run(["git", "-C", str(other), "rev-parse",  # noqa: S603
                              "chore/wip"], capture_output=True,
                             text=True).stdout.strip()
    stale = repo.git("rev-parse", "origin/chore/wip").stdout.strip()
    assert stale != new_tip
    repo.git("branch", "-D", "chore/wip")

    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode == 0, out.stderr
    assert repo.git("rev-parse", f"{TAG}^{{commit}}").stdout.strip() == new_tip


def test_a_rerun_is_a_noop(repo: Repo) -> None:
    first = _archive(repo, "chore/wip", "--reason", "done")
    assert first.returncode == 0, first.stderr
    tag_sha = repo.git("rev-parse", TAG).stdout.strip()
    second = _archive(repo, "chore/wip", "--reason", "done")
    assert second.returncode == 0, second.stderr
    assert "already archived" in second.stdout
    assert repo.git("rev-parse", TAG).stdout.strip() == tag_sha


def test_a_retry_after_a_failed_push_succeeds_unchanged(repo: Repo) -> None:
    _pre_push_hook(repo, "exit 1")
    first = _archive(repo, "chore/wip", "--reason", "done")
    assert first.returncode != 0
    tag_sha = repo.git("rev-parse", TAG).stdout.strip()
    _pre_push_hook(repo, "exit 0")
    second = _archive(repo, "chore/wip", "--reason", "done")
    assert second.returncode == 0, second.stderr
    assert "already archived" in second.stdout
    assert repo.git("rev-parse", TAG).stdout.strip() == tag_sha
    assert tag_sha in repo.git("ls-remote", "origin", TAG).stdout


def test_a_conflicting_tag_is_refused(repo: Repo) -> None:
    """An annotated tag on another commit is a different record."""
    repo.git("tag", "-a", "-m", "other record", "archive/chore/wip", "master")
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "already points at" in out.stderr
    assert repo.git("ls-remote", "origin", TAG).stdout.strip() == ""


def test_a_reused_branch_name_at_a_new_tip_is_refused(repo: Repo) -> None:
    """The old tag's record stays bound to the tip it was made for."""
    first = _archive(repo, "chore/wip", "--reason", "done")
    assert first.returncode == 0, first.stderr
    old_tip = repo.head("chore/wip")
    repo.git("checkout", "-q", "chore/wip")
    repo.git("commit", "-qm", "new work", "--allow-empty")
    repo.git("checkout", "-q", "master")
    assert repo.head("chore/wip") != old_tip
    out = _archive(repo, "chore/wip", "--reason", "again")
    assert out.returncode != 0
    assert "already points at" in out.stderr
    assert repo.git("rev-parse", f"{TAG}^{{commit}}").stdout.strip() == old_tip


def test_a_tag_recording_a_different_source_tip_is_refused(repo: Repo) -> None:
    """The source-tip field must equal the tag's own target."""
    tip = repo.head("chore/wip")
    master = repo.head("master")
    repo.git("tag", "-a", "-m",
             f"archive: chore/wip\n\nbranch: chore/wip\nsource-tip: {master}\n"
             f"worktree: -\nreason: forged",
             "archive/chore/wip", tip)
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "different source tip" in out.stderr


def test_a_lightweight_tag_is_refused(repo: Repo) -> None:
    """Only an annotated tag is the record; a name alone proves nothing."""
    repo.git("tag", "archive/chore/wip")
    out = _archive(repo, "chore/wip", "--reason", "done")
    assert out.returncode != 0
    assert "annotated" in out.stderr
    assert repo.git("cat-file", "-t", TAG).stdout.strip() == "commit"


def test_gc_removes_a_branch_whose_tip_the_archive_records(repo: Repo) -> None:
    """Custody by record: annotated, pushed, target and source-tip on the tip."""
    assert _archive(repo, "chore/wip", "--reason", "done").returncode == 0
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    assert _line(out, "local branch", "chore/wip").startswith("remove")
    assert _line(out, "remote branch", "origin/chore/wip").startswith("remove")
    log = (repo.tmp / "state" / "nora" / "git-gc.log").read_text()
    assert "archive=archive/chore/wip" in log
    assert "chore/wip" not in repo.git("branch", "--list", "chore/wip").stdout


def test_gc_keeps_a_branch_whose_tip_moved_since_the_archive(repo: Repo) -> None:
    """The tag records one tip exactly; a later commit is unrecorded work."""
    assert _archive(repo, "chore/wip", "--reason", "done").returncode == 0
    repo.git("checkout", "-q", "chore/wip")
    repo.git("commit", "-qm", "more", "--allow-empty")
    repo.git("checkout", "-q", "master")
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    line = _line(out, "local branch", "chore/wip")
    assert line.startswith("keep") and "unmerged" in line
    assert "chore/wip" in repo.git("branch", "--list", "chore/wip").stdout


def test_gc_grants_no_custody_for_a_local_only_tag(repo: Repo) -> None:
    """A tag never pushed to origin proves nothing durable."""
    tip = repo.head("chore/wip")
    repo.git("tag", "-a", "-m",
             f"archive: chore/wip\n\nbranch: chore/wip\nsource-tip: {tip}\n"
             f"worktree: -\nreason: local only",
             "archive/chore/wip", tip)
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    line = _line(out, "local branch", "chore/wip")
    assert line.startswith("keep") and "unmerged" in line
    assert "chore/wip" in repo.git("branch", "--list", "chore/wip").stdout


def test_gc_grants_no_custody_when_source_tip_differs_from_target(repo: Repo) -> None:
    """A pushed tag whose record does not match its target grants nothing."""
    tip = repo.head("chore/wip")
    master = repo.head("master")
    repo.git("tag", "-a", "-m",
             f"archive: chore/wip\n\nbranch: chore/wip\nsource-tip: {master}\n"
             f"worktree: -\nreason: mismatched",
             "archive/chore/wip", tip)
    repo.git("push", "-q", "origin", TAG)
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    line = _line(out, "local branch", "chore/wip")
    assert line.startswith("keep") and "unmerged" in line
    assert "chore/wip" in repo.git("branch", "--list", "chore/wip").stdout
