"""`scripts/git_settle.py` against real git: each outcome is verified, not asserted.

A throwaway repository with a bare `origin` holds a branch per outcome class;
the script runs as a subprocess (`make settle`'s invocation) and `gh` is a stub
on PATH answering `gh pr list --state merged` with real head SHAs. The
refusals — `abandoned` with unique commits, `merged` with no merged PR — are
the negative cases that keep an unrecorded drop from reading as a settle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests.infra.test_git_gc import Repo, _gh_stub

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "git_settle.py"


def _settle(repo: Repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this repo's own script
        [sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        env=repo.env,
        capture_output=True,
        text=True,
    )


def _log(repo: Repo) -> str:
    path = repo.tmp / "state" / "nora" / "git-gc.log"
    return path.read_text() if path.exists() else ""


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
    r.git("config", "user.email", "settle@test.invalid")
    r.git("config", "user.name", "settle test")
    (main / "tracked.txt").write_text("original\n")
    r.git("add", "tracked.txt")
    r.git("commit", "-qm", "init")
    r.git("remote", "add", "origin", str(origin))
    r.git("push", "-qu", "origin", "master")

    for branch in ("feat/landed", "chore/wip"):
        r.git("checkout", "-qb", branch)
        r.git("commit", "-qm", branch, "--allow-empty")
        r.git("push", "-qu", "origin", branch)
    # feat/landed's tip then merges into master: ancestry alone proves merged.
    r.git("checkout", "-q", "master")
    r.git("merge", "-q", "--no-ff", "feat/landed", "-m", "merge: landed")
    r.git("push", "-qu", "origin", "master")

    # feat/squashed never enters master's ancestry; its merged-PR head is custody.
    r.git("checkout", "-qb", "feat/squashed", "master")
    r.git("commit", "-qm", "squashed", "--allow-empty")
    r.git("push", "-qu", "origin", "feat/squashed")
    # br/idle sits at master's tip: no unique commits, so abandoning is honest.
    r.git("checkout", "-qb", "br/idle", "master")
    r.git("push", "-qu", "origin", "br/idle")
    r.git("checkout", "-q", "master")

    merged = [{"number": 9, "headRefOid": r.head("feat/squashed"),
               "headRefName": "feat/squashed"}]
    bin_dir = tmp_path / "bin"
    _gh_stub(bin_dir, "[]", json.dumps(merged))
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return r


def test_merged_is_recorded_for_a_merged_pr_head(repo: Repo) -> None:
    """feat/squashed is no ancestor of master; merged-PR head #9 is its custody."""
    sha = repo.head("feat/squashed")
    assert repo.git("merge-base", "--is-ancestor",
                    "feat/squashed", "master").returncode == 1
    out = _settle(repo, "feat/squashed", "merged")
    assert out.returncode == 0, out.stderr
    assert f"settle feat/squashed {sha} merged" in _log(repo)
    assert repo.git("branch", "--list", "feat/squashed").stdout.strip()


def test_merged_is_recorded_for_a_tip_in_master(repo: Repo) -> None:
    sha = repo.head("feat/landed")
    out = _settle(repo, "feat/landed", "merged")
    assert out.returncode == 0, out.stderr
    assert f"settle feat/landed {sha} merged" in _log(repo)


def test_merged_is_refused_without_a_merged_pr(repo: Repo) -> None:
    """chore/wip is unmerged and no PR head: 'merged' stays unrecorded."""
    out = _settle(repo, "chore/wip", "merged")
    assert out.returncode != 0
    assert "no merged PR" in out.stderr
    assert "chore/wip" not in _log(repo)


def test_merged_is_unavailable_when_gh_fails(repo: Repo) -> None:
    """A failed `gh` call is unavailable evidence, never a clean verdict."""
    (repo.tmp / "bin" / "gh").write_text("#!/bin/sh\nexit 1\n")
    out = _settle(repo, "chore/wip", "merged")
    assert out.returncode != 0
    assert "unavailable" in out.stderr
    assert "chore/wip" not in _log(repo)


def test_archived_pushes_an_annotated_tag_and_keeps_the_branch(repo: Repo) -> None:
    sha = repo.head("chore/wip")
    out = _settle(repo, "chore/wip", "archived")
    assert out.returncode == 0, out.stderr
    assert repo.git("rev-parse", "archive/chore/wip^{commit}").stdout.strip() == sha
    assert repo.git("cat-file", "-t", "archive/chore/wip").stdout.strip() == "tag"
    remote = repo.git("ls-remote", "origin", "refs/tags/archive/chore/wip")
    assert remote.stdout.split()[0] == repo.git("rev-parse", "archive/chore/wip").stdout.strip()
    assert repo.git("branch", "--list", "chore/wip").stdout.strip()  # never deletes
    assert f"settle chore/wip {sha} archived" in _log(repo)


def test_archived_saves_a_dirty_worktree_first(repo: Repo) -> None:
    wt = repo.tmp / "wt-dirty"
    repo.git("worktree", "add", "-q", str(wt), "chore/wip")
    (wt / "tracked.txt").write_text("touched\n")
    (wt / "scratch.txt").write_text("unsaved work\n")
    out = _settle(repo, "chore/wip", "archived")
    assert out.returncode == 0, out.stderr
    saved = repo.tmp / "state" / "nora" / "archive" / "chore" / "wip"
    assert "touched" in (saved / "diff.patch").read_text()
    with tarfile.open(saved / "untracked.tar") as tar:
        assert tar.getnames() == ["scratch.txt"]
    assert (wt / "scratch.txt").exists()  # settle never deletes


def test_abandoned_is_refused_when_the_branch_has_unique_commits(repo: Repo) -> None:
    """chore/wip carries a commit outside origin/master: abandoned is refused."""
    out = _settle(repo, "chore/wip", "abandoned", "--reason", "dropped")
    assert out.returncode != 0
    assert "refused" in out.stderr
    assert "chore/wip" not in _log(repo)
    assert repo.git("branch", "--list", "chore/wip").stdout.strip()


def test_abandoned_records_the_reason_when_no_commits_are_unique(repo: Repo) -> None:
    sha = repo.head("br/idle")
    out = _settle(repo, "br/idle", "abandoned", "--reason", "superseded by intake")
    assert out.returncode == 0, out.stderr
    assert f"settle br/idle {sha} abandoned superseded by intake" in _log(repo)


def test_abandoned_requires_a_reason(repo: Repo) -> None:
    out = _settle(repo, "br/idle", "abandoned")
    assert out.returncode != 0
    assert "br/idle" not in _log(repo)
