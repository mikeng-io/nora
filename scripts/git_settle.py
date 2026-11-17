"""Record how a finished artifact ended: merged, archived or abandoned.

`make settle BRANCH=<b> OUTCOME=<merged|archived|abandoned> [REASON=...]` runs
this once per finished handoff. Each outcome is verified, never asserted:

- `merged`: the tip is an ancestor of `origin/master`, or the branch name or
  tip is the head of a merged PR (`gh pr list --state merged` — a merge into
  a stacked base or a squash leaves no master ancestry, but GitHub keeps the
  head under `refs/pull/N/head`).
- `archived`: an annotated `archive/<branch>` tag is created at the tip and
  pushed to origin. A worktree still holding the branch's uncommitted work is
  saved first: `git diff HEAD` plus a tarball of untracked files under
  `${XDG_STATE_HOME:-~/.local/state}/nora/archive/<branch>/`.
- `abandoned`: only with a one-line `--reason`, and refused when the branch
  carries commits not reachable from `origin/master` — work with unique
  commits is archived or merged, never dropped unrecorded.

Nothing is deleted; retirement stays with `make gc`. Each outcome is appended
as one line to the same log `git_gc.py` writes:
`${XDG_STATE_HOME:-~/.local/state}/nora/git-gc.log`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

MERGE_BASE = "origin/master"
GH_PR_LIMIT = "1000"
GIT_TIMEOUT = 120


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed git argv, repo-local
            ["git", *args],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(("git", *args), 127, "", str(exc))


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "nora"


def _tip(repo: Path, branch: str) -> str | None:
    """The local branch tip, or the remote-tracking tip when no local branch."""
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        out = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        if out.returncode == 0:
            return out.stdout.strip()
    return None


def _merged_pr_heads(repo: Path) -> tuple[set[str], set[str]] | None:
    """(head names, head SHAs) of merged PRs; None when gh cannot answer."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed gh argv, repo-local
            [  # noqa: S607
                "gh", "pr", "list", "--state", "merged",
                "--json", "headRefName,headRefOid", "--limit", GH_PR_LIMIT,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        rows = json.loads(out.stdout)
        return (
            {str(r["headRefName"]) for r in rows},
            {str(r["headRefOid"]) for r in rows},
        )
    except (KeyError, TypeError, ValueError):
        return None


def _is_merged(repo: Path, branch: str, tip: str) -> bool | None:
    """None when a check protecting the claim could not run."""
    ancestor = _git(repo, "merge-base", "--is-ancestor", tip, MERGE_BASE)
    if ancestor.returncode == 0:
        return True
    if ancestor.returncode not in (0, 1):
        return None
    heads = _merged_pr_heads(repo)
    if heads is None:
        return None
    names, shas = heads
    return branch in names or tip in shas


def _worktree_for(repo: Path, branch: str) -> Path | None:
    """The worktree that has this branch checked out, if one is registered."""
    out = _git(repo, "worktree", "list", "--porcelain")
    if out.returncode != 0:
        return None
    path: Path | None = None
    for line in [*out.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}" and path is not None:
            return path
        elif not line:
            path = None
    return None


def _save_dirty(repo: Path, branch: str, worktree: Path) -> str:
    """Save uncommitted work beside the outcome; '' on success, else the error."""
    status = _git(repo, "-C", str(worktree), "status", "--porcelain")
    if status.returncode != 0:
        return f"git status failed ({status.stderr.strip() or status.returncode})"
    if not status.stdout.strip():
        return ""
    dest = _state_dir() / "archive" / branch
    try:
        dest.mkdir(parents=True, exist_ok=True)
        diff = _git(repo, "-C", str(worktree), "diff", "HEAD")
        if diff.returncode != 0:
            return f"git diff failed ({diff.stderr.strip() or diff.returncode})"
        (dest / "diff.patch").write_text(diff.stdout, encoding="utf-8")
        untracked = _git(
            repo, "-C", str(worktree), "ls-files", "--others", "--exclude-standard",
        )
        if untracked.returncode != 0:
            return f"git ls-files failed ({untracked.stderr.strip() or untracked.returncode})"
        names = [n for n in untracked.stdout.splitlines() if n]
        if names:
            with tarfile.open(dest / "untracked.tar", "w") as tar:
                for name in names:
                    tar.add(worktree / name, arcname=name)
    except OSError as exc:
        return str(exc)
    return ""


def _archive(repo: Path, branch: str, tip: str) -> str:
    """Tag and push the archive ref; '' on success, else the error."""
    tag = f"archive/{branch}"
    existing = _git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if existing.returncode == 0 and existing.stdout.strip() != tip:
        return f"{tag} already points at {existing.stdout.strip()[:12]}"
    if existing.returncode != 0:
        out = _git(repo, "tag", "-a", "-m", f"archive: {branch}", tag, tip)
        if out.returncode != 0:
            return f"git tag failed ({out.stderr.strip() or out.returncode})"
    pushed = _git(repo, "push", "origin", f"refs/tags/{tag}")
    if pushed.returncode != 0:
        return f"git push failed ({pushed.stderr.strip() or pushed.returncode})"
    return ""


def _has_unique_commits(repo: Path, tip: str) -> bool | None:
    """True when the tip carries commits unreachable from origin/master."""
    out = _git(repo, "rev-list", tip, "--not", MERGE_BASE)
    if out.returncode != 0:
        return None
    return bool(out.stdout.strip())


def _record(branch: str, tip: str, outcome: str, reason: str) -> None:
    log = _state_dir() / "git-gc.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).isoformat(timespec="seconds")
    line = f"{stamp} settle {branch} {tip} {outcome}"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"{line}{f' {reason}' if reason else ''}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("branch", help="branch whose outcome is recorded")
    parser.add_argument("outcome", choices=("merged", "archived", "abandoned"))
    parser.add_argument("--reason", default="", help="one-line reason (required for abandoned)")
    args = parser.parse_args(argv)

    if "\n" in args.reason:
        print("settle: --reason must be one line", file=sys.stderr)
        return 2
    if args.outcome == "abandoned" and not args.reason:
        print("settle: abandoned needs a one-line --reason", file=sys.stderr)
        return 2

    repo_out = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if repo_out.returncode != 0:
        print(f"settle: not a worktree: {repo_out.stderr.strip()}", file=sys.stderr)
        return 2
    repo = Path(os.path.realpath(repo_out.stdout.strip()))

    tip = _tip(repo, args.branch)
    if tip is None:
        print(f"settle: no such branch: {args.branch}", file=sys.stderr)
        return 2
    if _git(repo, "fetch", "--refmap=", "--prune", "origin",
            "+refs/heads/*:refs/remotes/origin/*").returncode != 0:
        print("settle: git fetch origin failed", file=sys.stderr)
        return 1

    if args.outcome == "merged":
        merged = _is_merged(repo, args.branch, tip)
        if merged is None:
            print(f"settle: merged-PR evidence unavailable for {args.branch}",
                  file=sys.stderr)
            return 1
        if not merged:
            print(f"settle: refused: no merged PR for {args.branch}", file=sys.stderr)
            return 1
    elif args.outcome == "archived":
        worktree = _worktree_for(repo, args.branch)
        if worktree is not None:
            error = _save_dirty(repo, args.branch, worktree)
            if error:
                print(f"settle: saving dirty worktree failed: {error}", file=sys.stderr)
                return 1
        error = _archive(repo, args.branch, tip)
        if error:
            print(f"settle: refused: {error}", file=sys.stderr)
            return 1
    else:  # abandoned
        unique = _has_unique_commits(repo, tip)
        if unique is None:
            print(f"settle: rev-list failed for {args.branch}", file=sys.stderr)
            return 1
        if unique:
            print(f"settle: refused: {args.branch} has commits not in "
                  f"{MERGE_BASE}; archive it instead", file=sys.stderr)
            return 1

    _record(args.branch, tip, args.outcome, args.reason)
    print(f"recorded {args.outcome} {args.branch} ({tip[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
