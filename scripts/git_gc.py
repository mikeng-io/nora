"""Retire merged branches and stale worktrees mechanically.

`make gc` prints the classification; `make gc APPLY=1` (this script's
`--apply`) performs the removals. Every remote branch, local branch and
registered worktree is judged `remove`, `keep: <reason>` or `unknown: <error>`.
`unknown` means the check protecting that artifact could not run — never that
nothing was found — and keeps the exit status nonzero.

Merged means the ref's commit is an ancestor of `origin/master`, or of the
head of an open PR whose base is not master (an integration branch). The
open-PR list comes from `gh pr list`; when gh cannot answer, every remote
branch is `unknown` and nothing remote is removed. Local branches not already
kept for another reason are `unknown` too — open-PR-head protection cannot be
verified — while worktrees are still judged against `origin/master`, which can
only over-report "unmerged".

A worktree leaves only when it is clean (`git status --porcelain` empty),
merged, and its HEAD reflog shows no activity in the last 24 hours. The
recency rule is what keeps a just-created worktree — clean, merged, zero
commits of its own — alive. Removals use `git worktree remove` without
`--force`, `git branch -D` and `git push origin --delete`, and each removed
ref is appended with its SHA to `$XDG_STATE_HOME/nora/git-gc.log` so it can be
restored.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PROTECTED_EXACT = frozenset({"master", "deployments"})
PROTECTED_PREFIXES = ("dependabot/",)
MERGE_BASE = "origin/master"
STALE_SECONDS = 24 * 60 * 60
GH_PR_LIMIT = "1000"
GIT_TIMEOUT = 120


@dataclass
class OpenPr:
    head_name: str
    head_sha: str
    base: str


@dataclass
class Worktree:
    path: Path
    head: str
    branch: str | None
    primary: bool
    bare: bool
    locked: bool
    prunable: bool


@dataclass
class Item:
    kind: str
    name: str
    sha: str
    verdict: str = "unknown"
    reason: str = "not classified"


@dataclass
class Context:
    repo: Path
    keep_paths: frozenset[Path]
    checked_out: frozenset[str]
    worktrees: list[Worktree]
    prs: list[OpenPr] | None
    fetch_ok: bool
    gh_error: str


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


def _protected(name: str) -> bool:
    return name in PROTECTED_EXACT or name.startswith(PROTECTED_PREFIXES)


def _worktrees(repo: Path) -> list[Worktree] | None:
    out = _git(repo, "worktree", "list", "--porcelain")
    if out.returncode != 0:
        return None
    trees: list[Worktree] = []
    entry: dict[str, str | bool] = {}
    for line in [*out.stdout.splitlines(), ""]:
        if not line:
            if "worktree" in entry:
                branch = entry.get("branch") or None
                trees.append(
                    Worktree(
                        path=Path(str(entry["worktree"])),
                        head=str(entry.get("HEAD", "")),
                        branch=str(branch).removeprefix("refs/heads/") if branch else None,
                        primary=not trees,
                        bare=bool(entry.get("bare")),
                        locked="locked" in entry,
                        prunable="prunable" in entry,
                    )
                )
            entry = {}
            continue
        key, _, value = line.partition(" ")
        entry[key] = value if value else True
    return trees


def _open_prs(repo: Path) -> tuple[list[OpenPr] | None, str]:
    try:
        out = subprocess.run(  # noqa: S603 - fixed gh argv, repo-local
            [  # noqa: S607
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,headRefName,headRefOid,baseRefName",
                "--limit",
                GH_PR_LIMIT,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if out.returncode != 0:
        return None, out.stderr.strip() or f"gh pr list exited {out.returncode}"
    try:
        rows = json.loads(out.stdout)
        return [
            OpenPr(str(r["headRefName"]), str(r["headRefOid"]), str(r["baseRefName"]))
            for r in rows
        ], ""
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"unparseable gh output: {exc}"


def _is_ancestor(repo: Path, commit: str, base: str) -> bool | None:
    out = _git(repo, "merge-base", "--is-ancestor", commit, base)
    if out.returncode == 0:
        return True
    if out.returncode == 1:
        return False
    return None


def _merged(repo: Path, commit: str, bases: list[str]) -> bool | None:
    """True when an ancestor of any base; None when a check could not run."""
    saw_error = False
    for base in bases:
        verdict = _is_ancestor(repo, commit, base)
        if verdict:
            return True
        if verdict is None:
            saw_error = True
    return None if saw_error else False


def _merge_bases(ctx: Context) -> list[str]:
    bases = [MERGE_BASE]
    for pr in ctx.prs or []:
        if pr.base != "master" and pr.head_sha:
            bases.append(pr.head_sha)
    return bases


def _is_pr_head(ctx: Context, name: str, sha: str) -> bool:
    return any(pr.head_name == name or pr.head_sha == sha for pr in ctx.prs or [])


def _classify_local_branch(ctx: Context, name: str, sha: str) -> Item:
    item = Item("local branch", name, sha)
    if _protected(name):
        item.verdict, item.reason = "keep", "protected"
    elif name in ctx.checked_out:
        item.verdict, item.reason = "keep", "checked out in a worktree"
    elif ctx.prs is None:
        item.reason = f"open PR list unavailable ({ctx.gh_error})"
    elif _is_pr_head(ctx, name, sha):
        item.verdict, item.reason = "keep", "head of an open PR"
    else:
        merged = _merged(ctx.repo, sha, _merge_bases(ctx))
        if merged:
            item.verdict, item.reason = "remove", "merged"
        elif merged is None:
            item.reason = "merge-base check failed"
        else:
            item.verdict, item.reason = "keep", "unmerged"
    return item


def _classify_remote_branch(ctx: Context, name: str, sha: str) -> Item:
    item = Item("remote branch", name, sha)
    short = name.removeprefix("origin/")
    if not ctx.fetch_ok:
        item.reason = "git fetch origin failed"
    elif ctx.prs is None:
        item.reason = f"open PR list unavailable ({ctx.gh_error})"
    elif _protected(short):
        item.verdict, item.reason = "keep", "protected"
    elif _is_pr_head(ctx, short, sha):
        item.verdict, item.reason = "keep", "head of an open PR"
    elif short in ctx.checked_out:
        item.verdict, item.reason = "keep", "checked out in a worktree"
    else:
        merged = _merged(ctx.repo, sha, _merge_bases(ctx))
        if merged:
            item.verdict, item.reason = "remove", "merged"
        elif merged is None:
            item.reason = "merge-base check failed"
        else:
            item.verdict, item.reason = "keep", "unmerged"
    return item


def _reflog_age(repo: Path, wt: Path) -> int | None:
    """Seconds since the newest HEAD reflog entry; None when unreadable."""
    out = _git(repo, "-C", str(wt), "reflog", "show", "-1", "--date=unix", "HEAD")
    if out.returncode != 0:
        return None
    match = re.search(r"@\{(\d+)\}", out.stdout)
    if not match:
        return None
    return int(datetime.now(tz=UTC).timestamp()) - int(match.group(1))


def _classify_worktree(ctx: Context, wt: Worktree, current: Path) -> Item:
    item = Item("worktree", str(wt.path), wt.head)
    real = Path(os.path.realpath(wt.path))
    if wt.primary:
        item.verdict, item.reason = "keep", "primary checkout"
    elif real == current:
        item.verdict, item.reason = "keep", "current worktree"
    elif real in ctx.keep_paths:
        item.verdict, item.reason = "keep", "--keep"
    elif wt.locked:
        item.verdict, item.reason = "keep", "locked"
    elif wt.bare:
        item.verdict, item.reason = "keep", "bare"
    elif wt.prunable:
        item.verdict, item.reason = "remove", "registration is stale"
    elif _git(ctx.repo, "-C", str(wt.path), "status", "--porcelain").stdout.strip():
        item.verdict, item.reason = "keep", "dirty"
    else:
        merged = _merged(ctx.repo, wt.head, _merge_bases(ctx))
        if merged is None:
            item.reason = "merge-base check failed"
        elif not merged:
            item.verdict = "keep"
            item.reason = "unmerged" if ctx.prs is not None else "not merged into origin/master"
        else:
            age = _reflog_age(ctx.repo, wt.path)
            if age is None:
                item.reason = "HEAD reflog unreadable"
            elif age < STALE_SECONDS:
                item.verdict, item.reason = "keep", "active within the last 24h"
            else:
                item.verdict, item.reason = "remove", "clean, merged and inactive"
    return item


def _enumerate(repo: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]] | None:
    branches: list[tuple[str, str]] = []
    remotes: list[tuple[str, str]] = []
    out = _git(repo, "for-each-ref", "refs/heads", "refs/remotes/origin",
               "--format=%(refname:short)%09%(objectname)%09%(symref)")
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        name, _, rest = line.partition("\t")
        sha, _, symref = rest.partition("\t")
        if not name or symref:  # skips refs/remotes/origin/HEAD, short name "origin"
            continue
        (remotes if name.startswith("origin/") else branches).append((name, sha))
    return branches, remotes


def _log_removed(log_path: Path, item: Item) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {item.kind} {item.name} {item.sha}\n")


def _apply(repo: Path, items: list[Item], log_path: Path) -> int:
    failures = 0
    for item in items:
        if item.verdict != "remove":
            continue
        if item.kind == "worktree":
            if item.reason == "registration is stale":
                continue
            out = _git(repo, "worktree", "remove", item.name)
        elif item.kind == "local branch":
            out = _git(repo, "branch", "-D", item.name)
        else:
            out = _git(repo, "push", "origin", "--delete", item.name.removeprefix("origin/"))
        if out.returncode == 0:
            _log_removed(log_path, item)
            print(f"  removed {item.kind} {item.name} ({item.sha[:12]})")
        else:
            failures += 1
            print(f"  error removing {item.kind} {item.name}: {out.stderr.strip()}",
                  file=sys.stderr)
    _git(repo, "worktree", "prune")
    return failures


def _state_log() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "nora" / "git-gc.log"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="perform removals (default: dry run)")
    parser.add_argument("--keep", action="append", default=[], metavar="PATH",
                        help="worktree path that must never be removed")
    args = parser.parse_args(argv)

    repo_out = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if repo_out.returncode != 0:
        print(f"git-gc: not a worktree: {repo_out.stderr.strip()}", file=sys.stderr)
        return 2
    repo = Path(os.path.realpath(repo_out.stdout.strip()))

    worktrees = _worktrees(repo)
    listing = _enumerate(repo)
    if worktrees is None or listing is None:
        print("git-gc: cannot enumerate worktrees or refs", file=sys.stderr)
        return 2
    branches, remotes = listing

    fetch_ok = _git(repo, "fetch", "origin").returncode == 0
    prs, gh_error = _open_prs(repo)
    ctx = Context(
        repo=repo,
        keep_paths=frozenset(Path(os.path.realpath(p)) for p in args.keep),
        checked_out=frozenset(wt.branch for wt in worktrees if wt.branch),
        worktrees=worktrees,
        prs=prs,
        fetch_ok=fetch_ok,
        gh_error=gh_error,
    )

    items = [_classify_remote_branch(ctx, n, s) for n, s in remotes]
    items += [_classify_local_branch(ctx, n, s) for n, s in branches]
    items += [_classify_worktree(ctx, wt, repo) for wt in worktrees]

    counts = {kind: sum(i.kind == kind for i in items)
              for kind in ("remote branch", "local branch", "worktree")}
    print(f"totals before: {counts['remote branch']} remote branches, "
          f"{counts['local branch']} local branches, {counts['worktree']} worktrees")
    for item in items:
        print(f"{item.verdict:8} {item.kind:14} {item.name} — {item.reason}")

    failures = _apply(repo, items, _state_log()) if args.apply else 0
    after = dict(counts)
    suffix = ""
    if args.apply:
        relisted = _enumerate(repo)
        trees = _worktrees(repo)
        if relisted is not None and trees is not None:
            after = {"remote branch": len(relisted[1]),
                     "local branch": len(relisted[0]),
                     "worktree": len(trees)}
        else:
            suffix = " (re-enumeration failed)"
    else:
        for kind in counts:
            after[kind] -= sum(i.kind == kind and i.verdict == "remove" for i in items)
        suffix = " (dry run; nothing removed)"
    print(f"totals after: {after['remote branch']} remote branches, "
          f"{after['local branch']} local branches, {after['worktree']} worktrees{suffix}")

    unknowns = sum(i.verdict == "unknown" for i in items)
    print(f"unknown: {unknowns}; removal errors: {failures}")
    return 1 if unknowns or failures else 0


if __name__ == "__main__":
    sys.exit(main())
