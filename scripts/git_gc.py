"""Retire merged branches and stale worktrees mechanically.

`make gc` prints the classification; `make gc APPLY=1` (this script's
`--apply`) performs the removals. Every remote branch, local branch and
registered worktree is judged `remove`, `keep: <reason>` or `unknown: <error>`.
`unknown` means the check protecting that artifact could not run — never that
nothing was found — and keeps the exit status nonzero.

Freshness: `git fetch --prune origin` and `git worktree list` run before refs are
enumerated; when the fetch fails, every verdict that depends on merge ancestry
is `unknown`. A ref is `merged` only when its tip is an ancestor of
`origin/master`, or its exact tip SHA is the head of a merged PR — GitHub keeps
`refs/pull/N/head`, so that commit stays retrievable and the PR number goes to
the removal log. The open-PR list comes from `gh pr list`; when gh cannot
answer, branch verdicts are `unknown` (open-PR-head protection cannot be
verified) and nothing remote or local is removed.

A worktree leaves only when it is clean (`git status --porcelain` empty),
merged, and its HEAD reflog shows no activity in the last 24 hours. The
recency rule is what keeps a just-created worktree — clean, merged, zero
commits of its own — alive. Removals are expected-value writes: remote
branches go through `git push --force-with-lease=<ref>:<sha> origin :<ref>`,
local branches through `git update-ref -d` guarded by the classified SHA, and
a worktree is removed only after re-checking that its HEAD and clean status
are unchanged. A worktree registration whose path is gone is removed by
deleting only its own `$GIT_COMMON_DIR/worktrees/<id>` admin dir — there is no
global `git worktree prune`, so a kept registration (for example `--keep`)
survives. Each removed ref is appended with its SHA to
`$XDG_STATE_HOME/nora/git-gc.log` so it can be restored.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
    pr: int | None = None  # merged-PR number when custody is via refs/pull/N/head


@dataclass
class Context:
    repo: Path
    keep_paths: frozenset[Path]
    checked_out: frozenset[str]
    open_prs: list[OpenPr] | None
    merged_prs: dict[str, int] | None  # head sha -> PR number
    fetch_ok: bool
    gh_open_error: str
    gh_merged_error: str


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


def _gh(repo: Path, state: str, fields: str) -> tuple[str | None, str]:
    try:
        out = subprocess.run(  # noqa: S603 - fixed gh argv, repo-local
            [  # noqa: S607
                "gh", "pr", "list", "--state", state,
                "--json", fields, "--limit", GH_PR_LIMIT,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if out.returncode != 0:
        return None, out.stderr.strip() or f"gh pr list --state {state} exited {out.returncode}"
    return out.stdout, ""


def _open_prs(repo: Path) -> tuple[list[OpenPr] | None, str]:
    raw, err = _gh(repo, "open", "headRefName,headRefOid")
    if raw is None:
        return None, err
    try:
        rows = json.loads(raw)
        return [
            OpenPr(str(r["headRefName"]), str(r["headRefOid"]))
            for r in rows
        ], ""
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"unparseable gh output: {exc}"


def _merged_pr_heads(repo: Path) -> tuple[dict[str, int] | None, str]:
    raw, err = _gh(repo, "merged", "number,headRefOid")
    if raw is None:
        return None, err
    try:
        rows = json.loads(raw)
        heads: dict[str, int] = {}
        for r in rows:  # gh returns newest first; keep the first hit
            heads.setdefault(str(r["headRefOid"]), int(r["number"]))
        return heads, ""
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"unparseable gh output: {exc}"


def _is_ancestor(repo: Path, commit: str, base: str) -> bool | None:
    out = _git(repo, "merge-base", "--is-ancestor", commit, base)
    if out.returncode == 0:
        return True
    if out.returncode == 1:
        return False
    return None


def _merged(ctx: Context, sha: str) -> tuple[bool | None, int | None]:
    """(merged, PR number): None when a check protecting the ref could not run."""
    if ctx.merged_prs is not None and sha in ctx.merged_prs:
        return True, ctx.merged_prs[sha]
    if not ctx.fetch_ok:
        return None, None  # ancestry verdicts need fresh refs
    verdict = _is_ancestor(ctx.repo, sha, MERGE_BASE)
    if verdict is True:
        return True, None
    if verdict is None or ctx.merged_prs is None:
        return None, None
    return False, None


def _merge_unknown_reason(ctx: Context) -> str:
    if not ctx.fetch_ok:
        return "git fetch origin failed"
    if ctx.merged_prs is None:
        return f"merged PR list unavailable ({ctx.gh_merged_error})"
    return "merge-base check failed"


def _is_pr_head(ctx: Context, name: str, sha: str) -> bool:
    return any(
        pr.head_name == name or pr.head_sha == sha for pr in ctx.open_prs or []
    )


def _classify_local_branch(ctx: Context, name: str, sha: str) -> Item:
    item = Item("local branch", name, sha)
    if _protected(name):
        item.verdict, item.reason = "keep", "protected"
    elif name in ctx.checked_out:
        item.verdict, item.reason = "keep", "checked out in a worktree"
    elif ctx.open_prs is None:
        item.reason = f"open PR list unavailable ({ctx.gh_open_error})"
    elif _is_pr_head(ctx, name, sha):
        item.verdict, item.reason = "keep", "head of an open PR"
    else:
        merged, pr = _merged(ctx, sha)
        if merged:
            item.verdict, item.reason = "remove", "merged"
            item.pr = pr
        elif merged is None:
            item.reason = _merge_unknown_reason(ctx)
        else:
            item.verdict, item.reason = "keep", "unmerged"
    return item


def _classify_remote_branch(ctx: Context, name: str, sha: str) -> Item:
    item = Item("remote branch", name, sha)
    short = name.removeprefix("origin/")
    if not ctx.fetch_ok:
        item.reason = "git fetch origin failed"  # tracking ref may be stale
    elif ctx.open_prs is None:
        item.reason = f"open PR list unavailable ({ctx.gh_open_error})"
    elif _protected(short):
        item.verdict, item.reason = "keep", "protected"
    elif _is_pr_head(ctx, short, sha):
        item.verdict, item.reason = "keep", "head of an open PR"
    elif short in ctx.checked_out:
        item.verdict, item.reason = "keep", "checked out in a worktree"
    else:
        merged, pr = _merged(ctx, sha)
        if merged:
            item.verdict, item.reason = "remove", "merged"
            item.pr = pr
        elif merged is None:
            item.reason = _merge_unknown_reason(ctx)
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
    else:
        status = _git(ctx.repo, "-C", str(wt.path), "status", "--porcelain")
        if status.returncode != 0:
            item.reason = f"git status failed ({status.stderr.strip() or status.returncode})"
            return item
        if status.stdout.strip():
            item.verdict, item.reason = "keep", "dirty"
            return item
        merged, pr = _merged(ctx, wt.head)
        if merged is None:
            item.reason = _merge_unknown_reason(ctx)
        elif not merged:
            item.verdict, item.reason = "keep", "unmerged"
        else:
            age = _reflog_age(ctx.repo, wt.path)
            if age is None:
                item.reason = "HEAD reflog unreadable"
            elif age < STALE_SECONDS:
                item.verdict, item.reason = "keep", "active within the last 24h"
            else:
                item.verdict, item.reason = "remove", "clean, merged and inactive"
                item.pr = pr
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


def _worktree_admin_dir(repo: Path, wt_path: Path) -> Path | None:
    """The $GIT_COMMON_DIR/worktrees/<id> dir whose gitdir points at wt_path."""
    out = _git(repo, "rev-parse", "--git-common-dir")
    if out.returncode != 0:
        return None
    common = Path(out.stdout.strip())
    if not common.is_absolute():
        common = Path(os.path.realpath(repo / common))
    wanted = os.path.realpath(wt_path / ".git")
    for gitdir in (common / "worktrees").glob("*/gitdir"):
        try:
            target = gitdir.read_text().strip()
        except OSError:
            continue
        if os.path.realpath(target) == wanted:
            return gitdir.parent
    return None


def _log_removed(log_path: Path, item: Item) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).isoformat(timespec="seconds")
    via = f" pr=#{item.pr}" if item.pr is not None else ""
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {item.kind} {item.name} {item.sha}{via}\n")


def _remove_worktree(repo: Path, item: Item) -> subprocess.CompletedProcess[str] | str:
    """Re-check HEAD and cleanliness before removing; 'changed' on mismatch."""
    head = _git(repo, "-C", item.name, "rev-parse", "HEAD")
    status = _git(repo, "-C", item.name, "status", "--porcelain")
    if (
        head.returncode != 0
        or status.returncode != 0
        or head.stdout.strip() != item.sha
        or status.stdout.strip()
    ):
        return "HEAD or clean status changed since classification"
    return _git(repo, "worktree", "remove", item.name)


def _apply(repo: Path, items: list[Item], log_path: Path) -> int:
    failures = 0
    for item in items:
        if item.verdict != "remove":
            continue
        if item.kind == "worktree":
            if item.reason == "registration is stale":
                admin = _worktree_admin_dir(repo, Path(item.name))
                if admin is None or Path(item.name).exists():
                    failures += 1
                    print(f"  error pruning {item.name}: admin dir not found or path is back",
                          file=sys.stderr)
                    continue
                try:
                    shutil.rmtree(admin)
                except OSError as exc:
                    failures += 1
                    print(f"  error pruning {item.name}: {exc}", file=sys.stderr)
                    continue
                _log_removed(log_path, item)
                print(f"  removed {item.kind} {item.name} ({item.sha[:12]})")
                continue
            checked = _remove_worktree(repo, item)
            if isinstance(checked, str):
                failures += 1
                print(f"  error removing worktree {item.name}: {checked}",
                      file=sys.stderr)
                continue
            out = checked
        elif item.kind == "local branch":
            out = _git(repo, "update-ref", "-d", f"refs/heads/{item.name}", item.sha)
        else:
            short = item.name.removeprefix("origin/")
            out = _git(repo, "push",
                       f"--force-with-lease=refs/heads/{short}:{item.sha}",
                       "origin", f":{short}")
        if out.returncode == 0:
            _log_removed(log_path, item)
            print(f"  removed {item.kind} {item.name} ({item.sha[:12]})")
        else:
            failures += 1
            print(f"  error removing {item.kind} {item.name}: {out.stderr.strip()}",
                  file=sys.stderr)
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

    fetch_ok = _git(repo, "fetch", "--prune", "origin").returncode == 0
    worktrees = _worktrees(repo)
    listing = _enumerate(repo)
    if worktrees is None or listing is None:
        print("git-gc: cannot enumerate worktrees or refs", file=sys.stderr)
        return 2
    branches, remotes = listing

    open_prs, gh_open_error = _open_prs(repo)
    merged_prs, gh_merged_error = _merged_pr_heads(repo)
    ctx = Context(
        repo=repo,
        keep_paths=frozenset(Path(os.path.realpath(p)) for p in args.keep),
        checked_out=frozenset(wt.branch for wt in worktrees if wt.branch),
        open_prs=open_prs,
        merged_prs=merged_prs,
        fetch_ok=fetch_ok,
        gh_open_error=gh_open_error,
        gh_merged_error=gh_merged_error,
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
