"""Archive a finished branch's work into one pushed annotated tag.

`make archive BRANCH=<b> REASON="..." [WORKTREE=<path>]` — the tag is the
record. An annotated `archive/<branch>` tag carries the reason, branch,
source tip and worktree path; it is pushed and verified against origin
before a given worktree is removed, and a re-run over the same commit is a
no-op. Every step fails closed on any failed git call:

- `git fetch --refmap= --prune origin +refs/heads/*:refs/remotes/origin/*`,
  then the tip resolves as the WORKTREE's HEAD, else the local branch, else
  `origin/<branch>`. A passed WORKTREE must be a registered worktree — a
  failed `git worktree list` aborts like any other call.
- A dirty worktree is snapshotted without touching it or its index:
  `read-tree HEAD`, `add -A`, `write-tree`, `commit-tree -p <tip>` under a
  temporary GIT_INDEX_FILE — binary- and filename-safe, no patch or
  tarball. The archived commit is the snapshot, otherwise the tip.
- The annotated `archive/<branch>` tag on that commit holds the record. An
  existing annotated tag on the same commit is a no-op; another target or a
  lightweight tag is refused — exactly-once by construction.
- `git push` the tag, then `git ls-remote` must echo the local tag object.
- Only then `git worktree remove --force` a passed WORKTREE — the one place
  a dirty worktree is removed, behind a verified durable copy.

Merged work's record is its merged PR, and work with no unique commits is
already an ancestor of master, so neither is tagged; `make gc` retires both
and counts `archive/<branch>` as custody when its recorded source tip is
the branch's tip.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GIT_TIMEOUT = 120
FETCH_ARGS = ("fetch", "--refmap=", "--prune", "origin",
              "+refs/heads/*:refs/remotes/origin/*")

# Fixed identity and date make the snapshot commit a pure function of the
# worktree's state, so a retried snapshot lands on the same SHA and the
# idempotent path sees the same commit.
_SNAPSHOT_ENV = {
    "GIT_AUTHOR_NAME": "nora archive",
    "GIT_AUTHOR_EMAIL": "archive@nora.invalid",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_NAME": "nora archive",
    "GIT_COMMITTER_EMAIL": "archive@nora.invalid",
    "GIT_COMMITTER_DATE": "@0 +0000",
}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None,
         ) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed git argv, repo-local
            ["git", *args],  # noqa: S607
            cwd=repo,
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(("git", *args), 127, "", str(exc))


def _fail(message: str, code: int = 1) -> int:
    print(f"archive: {message}", file=sys.stderr)
    return code


def _tag_kind(repo: Path, ref: str) -> str:
    """'tag' (annotated), 'commit' (lightweight) or '' when the ref is absent."""
    out = _git(repo, "cat-file", "-t", ref)
    return out.stdout.strip() if out.returncode == 0 else ""


def _tag_commit(repo: Path, ref: str) -> str | None:
    out = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    return out.stdout.strip() if out.returncode == 0 else None


def _tag_fields(repo: Path, ref: str) -> dict[str, str]:
    """The `key: value` lines of an annotated tag's message; {} when unreadable."""
    out = _git(repo, "for-each-ref", "--format=%(contents)", ref)
    if out.returncode != 0:
        return {}
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep and key and " " not in key:
            fields[key] = value.strip()
    return fields


def _registered_worktrees(repo: Path) -> list[Path] | None:
    """Real paths of registered worktrees; None when the listing failed."""
    out = _git(repo, "worktree", "list", "--porcelain")
    if out.returncode != 0:
        return None
    return [Path(os.path.realpath(line.removeprefix("worktree ")))
            for line in out.stdout.splitlines()
            if line.startswith("worktree ")]


def _snapshot(repo: Path, wt: Path, tip: str) -> tuple[str, str]:
    """(commit, error): the worktree's full state committed on top of tip."""
    with tempfile.TemporaryDirectory(prefix="nora-archive-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), **_SNAPSHOT_ENV}
        for step in (("read-tree", "HEAD"), ("add", "-A"), ("write-tree",)):
            out = _git(repo, "-C", str(wt), *step, env=env)
            if out.returncode != 0:
                return "", f"git {step[0]} failed ({out.stderr.strip()})"
        tree = out.stdout.strip()
    out = _git(repo, "-C", str(wt), "commit-tree", tree,
               "-p", tip, "-m", "archive snapshot")
    if out.returncode != 0:
        return "", f"git commit-tree failed ({out.stderr.strip()})"
    return out.stdout.strip(), ""


def _push_and_verify(repo: Path, tag: str, ref: str) -> int:
    """Push the tag and require the remote object to equal the local one."""
    pushed = _git(repo, "push", "origin", ref)
    if pushed.returncode != 0:
        return _fail(f"push of {tag} failed ({pushed.stderr.strip()})")
    local = _git(repo, "rev-parse", ref)
    remote = _git(repo, "ls-remote", "origin", ref)
    if local.returncode != 0 or remote.returncode != 0:
        return _fail(f"cannot verify {tag} on origin")
    found = next(
        (line.partition("\t")[0] for line in remote.stdout.splitlines()
         if line.partition("\t")[2] == ref), None)
    if found != local.stdout.strip():
        return _fail(f"remote {tag} is {found or 'missing'}, "
                     f"not {local.stdout.strip()[:12]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("branch", help="branch whose work is archived")
    parser.add_argument("--reason", required=True,
                        help="one-line reason recorded in the tag")
    parser.add_argument("--worktree", metavar="PATH",
                        help="registered worktree to snapshot and remove")
    args = parser.parse_args(argv)
    reason = args.reason.strip()
    if not reason or "\n" in reason:
        return _fail("a one-line --reason is required", 2)

    top = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return _fail(f"not a worktree: {top.stderr.strip()}", 2)
    repo = Path(os.path.realpath(top.stdout.strip()))

    # The refresh runs before the tip and any existing tag resolve so a
    # remote that advanced is what gets compared and archived, not stale
    # tracking refs.
    fetch = _git(repo, *FETCH_ARGS)
    if fetch.returncode != 0:
        return _fail(f"git fetch origin failed ({fetch.stderr.strip()})")

    tag = f"archive/{args.branch}"
    ref = f"refs/tags/{tag}"
    kind = _tag_kind(repo, ref)
    if kind == "commit":
        return _fail(f"{tag} exists and is not an annotated tag")
    prior = _tag_commit(repo, ref) if kind else None
    fields = _tag_fields(repo, ref) if kind == "tag" else {}

    wt: Path | None = None
    tip: str | None = None
    if args.worktree is not None:
        wt = Path(os.path.realpath(args.worktree))
        registered = _registered_worktrees(repo)
        if registered is None:
            return _fail("git worktree list failed")
        if wt not in registered:
            # An earlier run may already have archived and removed it.
            if (not wt.exists() and prior is not None
                    and fields.get("worktree") == str(wt)):
                rc = _push_and_verify(repo, tag, ref)
                if rc == 0:
                    print(f"already archived {args.branch} ({tag})")
                return rc
            return _fail(f"{wt} is not a registered worktree", 2)
        out = _git(repo, "-C", str(wt), "rev-parse", "--verify", "HEAD^{commit}")
        if out.returncode != 0:
            return _fail(f"cannot resolve HEAD in {wt}")
        tip = out.stdout.strip()
    else:
        for cand in (f"refs/heads/{args.branch}",
                     f"refs/remotes/origin/{args.branch}"):
            out = _git(repo, "rev-parse", "--verify", f"{cand}^{{commit}}")
            if out.returncode == 0:
                tip = out.stdout.strip()
                break
        if tip is None:
            return _fail(f"no such branch: {args.branch}", 2)

    archived = tip
    if wt is not None:
        status = _git(repo, "-C", str(wt), "status", "--porcelain")
        if status.returncode != 0:
            return _fail(f"git status failed ({status.stderr.strip()})")
        if status.stdout.strip():
            archived, error = _snapshot(repo, wt, tip)
            if error:
                return _fail(f"snapshot failed: {error}")

    if kind == "":
        message = (f"archive: {args.branch}\n\nbranch: {args.branch}\n"
                   f"source-tip: {tip}\n"
                   f"worktree: {wt if wt is not None else '-'}\n"
                   f"reason: {reason}")
        out = _git(repo, "tag", "-a", "-m", message, tag, archived)
        if out.returncode != 0:
            return _fail(f"git tag failed ({out.stderr.strip()})")
    elif prior != archived:
        return _fail(f"{tag} already points at {(prior or 'unreadable')[:12]}")

    rc = _push_and_verify(repo, tag, ref)
    if rc:
        return rc

    if wt is not None:
        out = _git(repo, "worktree", "remove", "--force", str(wt))
        if out.returncode != 0:
            return _fail(f"git worktree remove failed ({out.stderr.strip()})")

    verb = "archived" if kind == "" else "already archived"
    print(f"{verb} {args.branch} at {archived[:12]} ({tag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
