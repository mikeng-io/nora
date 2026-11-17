"""Archive a finished branch's committed tip into one pushed annotated tag.

`make archive BRANCH=<b> REASON="..." [WORKTREE=<path>]` — the tag is the
record. An annotated `archive/<branch>` tag carries the reason, branch,
source tip and worktree path; it is pushed and verified against origin, and
a re-run over the same tip is a no-op. Every step fails closed on any
failed git call:

- `git fetch --refmap= --prune origin +refs/heads/*:refs/remotes/origin/*`,
  then the tip resolves as the WORKTREE's HEAD, else the local branch, else
  `origin/<branch>`; `git worktree list` tells whether the branch is
  checked out, and its failure aborts.
- A worktree that holds the work — given with WORKTREE or found holding the
  branch — must be clean (`status --porcelain --ignored=no`): uncommitted
  work is refused with "commit or discard the work first". Ignored files go
  with the worktree when `make gc` retires it; they are build output by
  definition.
- An existing `archive/<branch>` is a no-op only when it is annotated, on
  the current tip, and records that tip as its source-tip — the tag's
  target IS the source tip; only committed state is archived. Another target,
  a lightweight tag or a mismatched source-tip is refused.
- `git push` the tag, then `git ls-remote` must echo the local tag object.

Merged work's record is its merged PR, and work with no unique commits is
already an ancestor of master, so neither is tagged; `make gc` retires
both and counts `archive/<branch>` as custody.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

GIT_TIMEOUT = 120
FETCH_ARGS = ("fetch", "--refmap=", "--prune", "origin",
              "+refs/heads/*:refs/remotes/origin/*")


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


def _worktrees(repo: Path) -> list[tuple[Path, str | None]] | None:
    """(path, checked-out branch) for each registered worktree; None on failure."""
    out = _git(repo, "worktree", "list", "--porcelain")
    if out.returncode != 0:
        return None
    trees: list[tuple[Path, str | None]] = []
    path: Path | None = None
    for line in [*out.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(os.path.realpath(line.removeprefix("worktree ")))
        elif line.startswith("branch refs/heads/") and path is not None:
            trees.append((path, line.removeprefix("branch refs/heads/")))
            path = None
        elif not line:
            if path is not None:
                trees.append((path, None))
            path = None
    return trees


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
    parser.add_argument("branch", help="branch whose tip is archived")
    parser.add_argument("--reason", required=True,
                        help="one-line reason recorded in the tag")
    parser.add_argument("--worktree", metavar="PATH",
                        help="registered worktree whose HEAD is the tip")
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

    trees = _worktrees(repo)
    if trees is None:
        return _fail("git worktree list failed")

    wt: Path | None = None
    tip: str | None = None
    if args.worktree is not None:
        wt = Path(os.path.realpath(args.worktree))
        if wt not in [path for path, _ in trees]:
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
        wt = next((path for path, branch in trees
                   if branch == args.branch), None)

    # The tag binds only what was committed; uncommitted work is refused,
    # and a failed status check aborts rather than reading as clean.
    if wt is not None:
        status = _git(repo, "-C", str(wt),
                      "status", "--porcelain", "--ignored=no")
        if status.returncode != 0:
            return _fail(f"git status failed ({status.stderr.strip()})")
        if status.stdout.strip():
            return _fail(f"{wt} has uncommitted work: "
                         "commit or discard the work first")

    if kind == "":
        message = (f"archive: {args.branch}\n\nbranch: {args.branch}\n"
                   f"source-tip: {tip}\n"
                   f"worktree: {wt if wt is not None else '-'}\n"
                   f"reason: {reason}")
        out = _git(repo, "tag", "-a", "-m", message, tag, tip)
        if out.returncode != 0:
            return _fail(f"git tag failed ({out.stderr.strip()})")
    elif prior != tip:
        return _fail(f"{tag} already points at {(prior or 'unreadable')[:12]}")
    elif fields.get("source-tip") != tip:
        return _fail(f"{tag} records a different source tip")

    rc = _push_and_verify(repo, tag, ref)
    if rc:
        return rc

    verb = "archived" if kind == "" else "already archived"
    print(f"{verb} {args.branch} at {tip[:12]} ({tag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
