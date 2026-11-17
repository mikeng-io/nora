"""`scripts/git_gc.py` against real git: every keep/remove/unknown rule.

Each test builds a throwaway repository with a bare `origin`, branches in
every protection class and worktrees in every keep/remove class, then runs
the script as a subprocess (`make gc`'s invocation). `gh` is a stub on PATH
that answers `gh pr list --state open|merged` with real head SHAs or exits
nonzero — the process boundary, never a fake of git. A worktree is aged with
`GIT_COMMITTER_DATE`, which is what the HEAD reflog records. Mid-run races
are injected with a real `pre-push` hook: the script's own remote deletion
pushes are what move a local ref or a worktree between classification and
removal.
"""

from __future__ import annotations

import json
import os
import shutil
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

    def worktrees(self) -> str:
        return self.git("worktree", "list", "--porcelain").stdout


def _gh_stub(bin_dir: Path, open_body: str, merged_body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        f"  *\"--state open\"*) printf '%s' '{open_body}' ;;\n"
        f"  *\"--state merged\"*) printf '%s' '{merged_body}' ;;\n"
        "  *) exit 2 ;;\nesac\n"
    )
    stub.chmod(0o755)


def _pre_push_hook(repo: Repo, body: str) -> None:
    hook = repo.path / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\ncat >/dev/null\n{body}\n")
    hook.chmod(0o755)


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

    # feat/squashed carries work that never lands in master's ancestry (a
    # squash merge); custody comes from its tip being a merged PR's head.
    r.git("checkout", "-qb", "feat/squashed")
    r.git("commit", "-qm", "squashed work", "--allow-empty")
    r.git("push", "-qu", "origin", "feat/squashed")
    r.git("checkout", "-q", "master")

    # feat/restacked's tip is a merged PR head AND an open PR head (the PR
    # was reopened/retargeted): the open-PR guard is what must keep it.
    r.git("checkout", "-qb", "feat/restacked")
    r.git("commit", "-qm", "restacked", "--allow-empty")
    r.git("push", "-qu", "origin", "feat/restacked")
    r.git("checkout", "-q", "master")

    # An open same-repo PR head with its own commit (shared-SHA heads would
    # pr-head-match every other branch at that commit).
    r.git("checkout", "-qb", "feat/pr-head")
    r.git("commit", "-qm", "pr", "--allow-empty")
    r.git("push", "-qu", "origin", "feat/pr-head")
    r.git("checkout", "-q", "master")

    # integ is an open PR whose base is not master; chore/in-integ is merged
    # into its head but not into master and not a merged-PR head, so under
    # the custody rule it must be kept — an open PR head is not custody.
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

    open_prs = [
        {"headRefName": "feat/pr-head",
         "headRefOid": r.head("feat/pr-head")},
        {"headRefName": "integ",
         "headRefOid": r.head("origin/integ")},
        {"headRefName": "feat/restacked",
         "headRefOid": r.head("feat/restacked")},
    ]
    merged_prs = [
        {"number": 5, "headRefOid": r.head("feat/squashed")},
        {"number": 7, "headRefOid": r.head("feat/restacked")},
    ]
    bin_dir = tmp_path / "bin"
    _gh_stub(bin_dir, json.dumps(open_prs), json.dumps(merged_prs))
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    wt = tmp_path / "wt"
    for name in ("stale", "recent", "dirty", "keep", "locked", "broken"):
        r.git("worktree", "add", "-q", str(wt / name), "-b", f"br/{name}", "master")
    r.git("worktree", "add", "-q", str(wt / "unmerged"), "chore/unmerged")
    for name in ("stale", "dirty", "unmerged", "keep", "locked", "broken"):
        _age(r, wt / name)
    (wt / "dirty" / "scratch.txt").write_text("dirty")
    r.git("worktree", "lock", str(wt / "locked"))
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


def test_locked_worktree_is_kept(repo: Repo) -> None:
    wt = repo.tmp / "wt" / "locked"
    out = _gc(repo, "--apply")
    assert "— locked" in _line(out, "worktree", str(wt.resolve()))
    assert wt.exists()


def test_failing_status_check_is_unknown(repo: Repo) -> None:
    """A `git status` failure is `unknown`, never read as clean."""
    wt = repo.tmp / "wt" / "broken"
    # .git file points at a nonexistent admin dir: still registered (the
    # gitdir backlink exists), but `git -C <wt> status` fails.
    (wt / ".git").write_text("gitdir: /nonexistent-gc-admin\n")
    assert repo.git("-C", str(wt), "status", "--porcelain").returncode != 0
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "git status failed" in _line(out, "worktree", str(wt.resolve()))
    assert wt.exists()
    assert not (repo.tmp / "wt" / "stale").exists()  # positive control


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


def test_branch_whose_tip_is_a_merged_pr_head_is_removed(repo: Repo) -> None:
    """feat/squashed is no ancestor of master; its tip is merged-PR head #5."""
    sha = repo.head("feat/squashed")
    assert repo.git("merge-base", "--is-ancestor",
                    "feat/squashed", "master").returncode == 1
    out = _gc(repo, "--apply")
    assert out.returncode == 0, out.stderr
    assert repo.git("branch", "--list", "feat/squashed").stdout == ""
    assert repo.git("ls-remote", "origin", "refs/heads/feat/squashed").stdout == ""
    log = (repo.tmp / "state" / "nora" / "git-gc.log").read_text()
    assert f"local branch feat/squashed {sha} pr=#5" in log
    assert f"remote branch origin/feat/squashed {sha} pr=#5" in log


def test_branch_merged_only_into_an_open_pr_head_is_kept(repo: Repo) -> None:
    """An open non-master PR head is not custody — the branch can be restacked."""
    assert repo.git("merge-base", "--is-ancestor",
                    "chore/in-integ", "origin/integ").returncode == 0
    assert repo.git("merge-base", "--is-ancestor",
                    "chore/in-integ", "master").returncode == 1
    out = _gc(repo, "--apply")
    assert "unmerged" in _line(out, "local branch", "chore/in-integ")
    assert repo.git("branch", "--list", "chore/in-integ").stdout.strip()
    assert repo.git("ls-remote", "origin", "refs/heads/chore/in-integ").stdout.strip()


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


def test_open_pr_head_wins_over_merged_head_custody(repo: Repo) -> None:
    """feat/restacked's tip is merged-PR head #7 AND an open head: kept by the guard."""
    out = _gc(repo, "--apply")
    assert "head of an open PR" in _line(out, "local branch", "feat/restacked")
    assert "head of an open PR" in _line(out, "remote branch", "origin/feat/restacked")
    assert repo.git("branch", "--list", "feat/restacked").stdout.strip()
    assert repo.git("ls-remote", "origin", "refs/heads/feat/restacked").stdout.strip()


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


def test_fetch_failure_makes_merge_verdicts_unknown(repo: Repo) -> None:
    """Every ancestry-dependent verdict is unknown when fetch cannot run."""
    repo.git("remote", "set-url", "origin", str(repo.tmp / "gone.git"))
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "git fetch origin failed" in _line(out, "local branch", "chore/merged")
    assert "git fetch origin failed" in _line(
        out, "worktree", str((repo.tmp / "wt" / "stale").resolve()))
    remote_lines = [
        ln for ln in out.stdout.splitlines()
        if "remote branch" in ln and ln.split()[:1] == ["unknown"]
    ]
    assert remote_lines
    assert repo.git("branch", "--list", "chore/merged").stdout.strip()
    assert (repo.tmp / "wt" / "stale").exists()


def test_merged_pr_list_failure_makes_nonancestors_unknown(repo: Repo) -> None:
    """Custody can't be proven without the merged list — unverified, not unmerged."""
    stub = repo.tmp / "bin" / "gh"
    stub.write_text(
        "#!/bin/sh\ncase \"$*\" in\n"
        "  *\"--state open\"*) printf '%s' '[]' ;;\n"
        "  *) exit 1 ;;\nesac\n"
    )
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "merged PR list unavailable" in _line(out, "local branch", "chore/in-integ")
    assert repo.git("branch", "--list", "chore/in-integ").stdout.strip()
    # An ancestor of master needs no PR data and is still removed.
    assert repo.git("branch", "--list", "chore/merged").stdout == ""


def test_stale_remote_ref_is_refused_by_the_lease(repo: Repo) -> None:
    """Upstream advanced after our tracking ref: the lease refuses the delete."""
    sha = repo.head("master")
    repo.git("push", "-q", "origin", "master:refs/heads/victim/remote")
    # A narrow fetch refspec leaves refs/remotes/origin/victim/remote stale.
    repo.git("config", "remote.origin.fetch",
             "+refs/heads/master:refs/remotes/origin/master")
    repo.git("update-ref", "refs/remotes/origin/victim/remote", sha)
    side = repo.tmp / "side"
    subprocess.run(  # noqa: S603 - fixed argv, throwaway clone
        ["git", "clone", "-q", str(repo.tmp / "origin.git"), str(side)], check=True)
    s = Repo(side, repo.env)
    s.git("config", "user.email", "gc@test.invalid")
    s.git("config", "user.name", "gc test")
    s.git("checkout", "-qb", "victim/remote", "origin/victim/remote")
    s.git("commit", "-qm", "adv", "--allow-empty")
    s.git("push", "-q", "origin", "victim/remote")
    assert repo.head("origin/victim/remote") == sha  # tracking ref is stale
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "error removing remote branch origin/victim/remote" in out.stderr
    assert repo.git("ls-remote", "origin",
                    "refs/heads/victim/remote").stdout.strip()
    log = (repo.tmp / "state" / "nora" / "git-gc.log").read_text()
    assert "victim/remote" not in log


def test_stale_local_branch_is_refused_by_update_ref(repo: Repo) -> None:
    """A local ref advanced after classification: update-ref -d refuses."""
    repo.git("branch", "victim/local", "master")
    tree = repo.git("rev-parse", "master^{tree}").stdout.strip()
    adv = repo.git("commit-tree", tree, "-p", "master", "-m", "adv").stdout.strip()
    # The script's own remote deletes fire this hook, moving the ref mid-run.
    _pre_push_hook(repo, f"git update-ref refs/heads/victim/local {adv}")
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "error removing local branch victim/local" in out.stderr
    assert repo.head("victim/local") == adv


def test_worktree_dirtied_mid_run_is_not_removed(repo: Repo) -> None:
    """The clean re-check catches a file that appeared after classification."""
    wt = repo.tmp / "wt" / "stale"
    _pre_push_hook(repo, f"echo x > '{wt.resolve()}/hook-moved'")
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "changed since classification" in out.stderr
    assert wt.exists()


def test_worktree_head_moved_mid_run_is_not_removed(repo: Repo) -> None:
    """The HEAD re-check catches a commit that landed after classification."""
    wt = repo.tmp / "wt" / "stale"
    _pre_push_hook(
        repo,
        f"env -u GIT_DIR -u GIT_WORK_TREE git -C '{wt.resolve()}'"
        " commit -qm moved --allow-empty")
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "changed since classification" in out.stderr
    assert wt.exists()


def test_stale_registration_removed_but_keep_path_stays(repo: Repo) -> None:
    """Only the `remove` registration is pruned; a kept stale one survives."""
    gone = repo.tmp / "wt" / "stale-reg"
    keep = repo.tmp / "wt" / "keep-reg"
    repo.git("worktree", "add", "-q", str(gone), "-b", "br/reg", "master")
    repo.git("worktree", "add", "-q", str(keep), "-b", "br/keepreg", "master")
    shutil.rmtree(gone)
    shutil.rmtree(keep)
    out = _gc(repo, "--apply", "--keep", str(keep))
    assert out.returncode == 0, out.stderr
    assert "registration is stale" in _line(out, "worktree", str(gone.resolve()))
    assert "— --keep" in _line(out, "worktree", str(keep.resolve()))
    listed = repo.worktrees()
    assert str(gone.resolve()) not in listed
    assert str(keep.resolve()) in listed


def test_failed_delete_of_an_absent_remote_branch_is_an_error(repo: Repo) -> None:
    """A delete that fails stays a failure, even when the branch is already gone."""
    sha = repo.head("master")
    repo.git("config", "remote.origin.fetch",
             "+refs/heads/master:refs/remotes/origin/master")
    repo.git("update-ref", "refs/remotes/origin/ghost/remote", sha)
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert "error removing remote branch origin/ghost/remote" in out.stderr
    log = repo.tmp / "state" / "nora" / "git-gc.log"
    assert not log.exists() or "ghost/remote" not in log.read_text()


def test_stale_registration_whose_path_returns_is_not_pruned(repo: Repo) -> None:
    """The path came back after classification: its registration is kept."""
    back = repo.tmp / "wt" / "back-reg"
    repo.git("worktree", "add", "-q", str(back), "-b", "br/backreg", "master")
    shutil.rmtree(back)
    _pre_push_hook(repo, f"mkdir -p '{back.resolve()}'")
    out = _gc(repo, "--apply")
    assert out.returncode != 0
    assert f"error pruning {back.resolve()}" in out.stderr
    assert str(back.resolve()) in repo.worktrees()
