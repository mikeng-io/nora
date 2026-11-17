"""`scripts/git_gc.py` against real git: every keep/remove rule.

Each test builds a throwaway repository with a bare `origin`, branches in
every protection class and worktrees in every keep/remove class, then runs
the script as a subprocess (`make gc`'s invocation). `gh` is a stub on PATH
that answers `gh pr list` with real head SHAs or exits 1 — the process
boundary, never a fake of git. A worktree is aged with `GIT_COMMITTER_DATE`,
which is what the HEAD reflog records.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "git_gc.py"
STALE = 100_000  # seconds; just over the 24h recency window


class Repo:
    def __init__(self, path: Path, env: dict[str, str]):
        self.path, self.env = path, env
        self.tmp = path.parent

    def git(self, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(  # noqa: S603 - fixed argv against a throwaway repo
            ["git", *args],
            cwd=self.path,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
        )

    def head(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def worktrees(self) -> list[str]:
        return [
            line.split()[0]
            for line in self.git("worktree", "list").stdout.splitlines()
        ]


def _gh_stub(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        f"  \"pr list\"*) printf '%s' '{body}' ;;\n"
        "  *) exit 2 ;;\nesac\n"
    )
    stub.chmod(0o755)


def _gc(repo: Repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this repo's own script
        [sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        env=repo.env,
        capture_output=True,
        text=True,
    )


def _age(repo: Repo, wt: Path) -> None:
    """Newest HEAD reflog entry lands >24h ago, like a worktree left untouched."""
    stamp = f"@{int(time.time()) - STALE} +0000"
    out = repo.git("-C", str(wt), "reset", "-q", "HEAD",
                   env={"GIT_COMMITTER_DATE": stamp})
    assert out.returncode == 0, out.stderr


def _line(out: subprocess.CompletedProcess[str], kind: str, name: str) -> str:
    """The classification line for one artifact: `<verdict> <kind> <name> — <why>`."""
    kw = kind.split()
    for ln in out.stdout.splitlines():
        parts = ln.split()
        if parts[1:1 + len(kw)] == kw and parts[1 + len(kw):2 + len(kw)] == [name]:
            return ln.strip()
    return ""


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
    r.git("config", "user.email", "gc@test.invalid")
    r.git("config", "user.name", "gc test")
    r.git("commit", "-qm", "init", "--allow-empty")
    r.git("remote", "add", "origin", str(origin))
    r.git("push", "-qu", "origin", "master")

    for branch in ("chore/merged", "deployments", "dependabot/pip/x"):
        r.git("branch", branch)
        r.git("push", "-qu", "origin", branch)

    r.git("checkout", "-qb", "chore/unmerged")
    r.git("commit", "-qm", "wip", "--allow-empty")
    r.git("push", "-qu", "origin", "chore/unmerged")
    r.git("checkout", "-q", "master")

    # An open same-repo PR head with its own commit (shared-SHA heads would
    # pr-head-match every other branch at that commit).
    r.git("checkout", "-qb", "feat/pr-head")
    r.git("commit", "-qm", "pr", "--allow-empty")
    r.git("push", "-qu", "origin", "feat/pr-head")
    r.git("checkout", "-q", "master")

    # integ is an open PR whose base is not master; chore/in-integ is merged
    # into its head (--no-ff, so the branch sits strictly below the head).
    r.git("checkout", "-qb", "integ")
    r.git("commit", "-qm", "i1", "--allow-empty")
    r.git("push", "-qu", "origin", "integ")
    r.git("checkout", "-qb", "chore/in-integ")
    r.git("commit", "-qm", "i2", "--allow-empty")
    r.git("push", "-qu", "origin", "chore/in-integ")
    r.git("checkout", "-q", "integ")
    r.git("merge", "-q", "--no-ff", "chore/in-integ", "-m", "merge: in-integ")
    r.git("push", "-qu", "origin", "integ")
    r.git("checkout", "-q", "master")

    prs = [
        {"number": 1, "headRefName": "feat/pr-head",
         "headRefOid": r.head("feat/pr-head"), "baseRefName": "master"},
        {"number": 2, "headRefName": "integ",
         "headRefOid": r.head("origin/integ"), "baseRefName": "feat/other"},
    ]
    bin_dir = tmp_path / "bin"
    _gh_stub(bin_dir, json.dumps(prs))
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    wt = tmp_path / "wt"
    r.git("worktree", "add", "-q", str(wt / "stale"), "-b", "br/stale", "master")
    r.git("worktree", "add", "-q", str(wt / "recent"), "-b", "br/recent", "master")
    r.git("worktree", "add", "-q", str(wt / "dirty"), "-b", "br/dirty", "master")
    r.git("worktree", "add", "-q", str(wt / "unmerged"), "chore/unmerged")
    r.git("worktree", "add", "-q", str(wt / "keep"), "-b", "br/keep", "master")
    for name in ("stale", "dirty", "unmerged", "keep"):
        _age(r, wt / name)
    (wt / "dirty" / "scratch.txt").write_text("dirty")
    return r


def test_merged_clean_inactive_worktree_is_removed(repo: Repo) -> None:
    wt = repo.tmp / "wt" / "stale"
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    assert "removed worktree" in out.stdout
    assert not wt.exists()
    assert str(wt.resolve()) not in repo.worktrees()
    # The worktree goes; its branch survives for the next gc pass.
    assert "br/stale" in repo.git("branch", "--list", "br/stale").stdout


def test_new_worktree_with_no_commits_is_kept(repo: Repo) -> None:
    """Clean and merged, but its reflog is minutes old — recency protects it."""
    wt = repo.tmp / "wt" / "recent"
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    assert "active within the last 24h" in _line(out, "worktree", str(wt.resolve()))
    assert wt.exists()


def test_dirty_worktree_is_kept(repo: Repo) -> None:
    wt = repo.tmp / "wt" / "dirty"
    out = _gc(repo, "--apply")
    assert "— dirty" in _line(out, "worktree", str(wt.resolve()))
    assert wt.exists()


def test_unmerged_worktree_is_kept(repo: Repo) -> None:
    wt = repo.tmp / "wt" / "unmerged"
    out = _gc(repo, "--apply")
    assert "— unmerged" in _line(out, "worktree", str(wt.resolve()))
    assert wt.exists()


def test_keep_path_is_kept(repo: Repo) -> None:
    wt = repo.tmp / "wt" / "keep"
    out = _gc(repo, "--apply", "--keep", str(wt))
    assert "— --keep" in _line(out, "worktree", str(wt.resolve()))
    assert wt.exists()
    assert not (repo.tmp / "wt" / "stale").exists()  # positive control


def test_merged_local_and_remote_branches_are_removed(repo: Repo) -> None:
    sha = repo.head("chore/merged")
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    assert repo.git("branch", "--list", "chore/merged").stdout == ""
    assert repo.git("ls-remote", "origin", "refs/heads/chore/merged").stdout == ""
    log = (repo.tmp / "state" / "nora" / "git-gc.log").read_text()
    assert f"local branch chore/merged {sha}" in log
    assert f"remote branch origin/chore/merged {sha}" in log


def test_protected_branches_are_kept(repo: Repo) -> None:
    out = _gc(repo, "--apply")
    for name in ("deployments", "dependabot/pip/x"):
        assert "protected" in _line(out, "local branch", name)
        assert repo.git("branch", "--list", name).stdout.strip()
    assert repo.git("ls-remote", "origin", "refs/heads/deployments").stdout.strip()


def test_open_pr_heads_are_kept(repo: Repo) -> None:
    out = _gc(repo, "--apply")
    for name in ("feat/pr-head", "integ"):
        assert "head of an open PR" in _line(out, "local branch", name)
        assert repo.git("branch", "--list", name).stdout.strip()


def test_branch_merged_into_an_integration_pr_head_is_removed(repo: Repo) -> None:
    """Merged needn't mean master: ancestor of an open non-master PR head."""
    assert repo.git("merge-base", "--is-ancestor",
                    "chore/in-integ", "origin/master").returncode == 1
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    assert repo.git("branch", "--list", "chore/in-integ").stdout == ""
    assert repo.git("ls-remote", "origin", "refs/heads/chore/in-integ").stdout == ""


def test_gh_failure_makes_remote_branches_unknown_and_removes_nothing(repo: Repo) -> None:
    (repo.tmp / "bin" / "gh").write_text("#!/bin/sh\nexit 1\n")
    remote_before = repo.git("ls-remote", "origin").stdout
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    remote_lines = [
        ln for ln in out.stdout.splitlines()
        if ln.split()[:1] in (["keep"], ["remove"], ["unknown"])
        and "remote branch" in ln
    ]
    assert remote_lines
    assert all(ln.startswith("unknown") for ln in remote_lines)
    assert repo.git("ls-remote", "origin").stdout == remote_before
    assert repo.git("branch", "--list", "chore/merged").stdout.strip()
