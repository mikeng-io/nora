#!/usr/bin/env python3
"""The only tool the identifier adjudicator's agent gets: a read-only view of
the PUBLISHED repository it runs in, with every denylisted value masked.

The first review of #591 gave the agent Read/Grep/Glob and `git` limited to
read subcommands. The second showed that was not read-only (`git grep
--open-files-in-pager=<cmd>` runs a program, `git log --output=<file>` writes
one, `git diff --no-index` reads outside the repository) and that anything
it read reached a hosted model unmasked. This replaces all of it: fixed
subcommands, no flags passed through, paths confined to the repository, and
every line holding a denylisted value -- read the way the scanner reads it --
replaced before it is printed.

    pii_readonly.py ls [<dir>]
    pii_readonly.py cat <path> [<first line> [<last line>]]
    pii_readonly.py grep <python regex> [<path or dir>]
    pii_readonly.py log [<path>]
    pii_readonly.py log-S <text> [<path>]      # commits that added or removed <text>
    pii_readonly.py show <commit> [<path>]
    pii_readonly.py blame <path>

The repository is the working directory. Output is capped; a refused request
says why and exits 2.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# bytecode is cached outside the worktree, never in scripts/__pycache__
if not sys.pycache_prefix:
    sys.pycache_prefix = os.path.join(os.environ.get("TMPDIR") or "/tmp", "nora-pii-pycache")  # noqa: S108

MAX_LINES = 400
MAX_BYTES = 2 * 1024 * 1024
MAX_MATCHES = 200
GIT_CONFIG = [
    "-c",
    "core.pager=cat",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "diff.external=",
    "-c",
    "core.hooksPath=/dev/null",
    "--no-pager",
]


class Refused(Exception):  # noqa: N818 - a refusal, reported to the agent
    pass


def root() -> str:
    return os.path.realpath(os.getcwd())


def inside(path: str) -> str:
    """A repository-relative path that stays inside the repository and out of
    `.git`; anything else is refused."""
    if not path or path.startswith("-") or "\0" in path:
        raise Refused(f"not a path: {path!r}")
    full = os.path.realpath(os.path.join(root(), path))
    if full != root() and not full.startswith(root() + os.sep):
        raise Refused("outside the repository")
    rel = os.path.relpath(full, root())
    if rel == ".git" or rel.startswith(".git" + os.sep):
        raise Refused("inside .git")
    return rel


def git(*args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        GIT_PAGER="cat",
        PAGER="cat",
    )
    p = subprocess.run(  # noqa: S603 - fixed subcommands, validated arguments
        ["git", *GIT_CONFIG, *args],  # noqa: S607
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if p.returncode != 0:
        raise Refused(p.stderr.decode(errors="replace").strip()[:300])
    return p.stdout[:MAX_BYTES].decode("utf-8", "replace")


def commit_id(ref: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{7,40}|HEAD(~\d{1,3})?", ref):
        raise Refused("a commit is a hex id or HEAD~N")
    return ref


def tracked(under: str = ".") -> list[str]:
    return [p for p in git("ls-files", "-z", "--", under).split("\0") if p]


def cmd(argv: list[str]) -> str:
    if not argv:
        raise Refused(__doc__ or "")
    op, args = argv[0], argv[1:]
    if op == "ls":
        d = inside(args[0]) if args else "."
        seen = sorted(
            {p[len(d) + 1 :].split("/")[0] if d != "." else p.split("/")[0] for p in tracked(d)}
        )
        return "\n".join(seen)
    if op == "cat" and args:
        rel = inside(args[0])
        a = int(args[1]) if len(args) > 1 else 1
        b = int(args[2]) if len(args) > 2 else a + MAX_LINES - 1
        with open(os.path.join(root(), rel), "rb") as fh:
            lines = fh.read(MAX_BYTES).decode("utf-8", "replace").split("\n")
        b = min(b, a + MAX_LINES - 1, len(lines))
        return "\n".join(f"{n}: {lines[n - 1]}" for n in range(max(1, a), b + 1))
    if op == "grep" and args:
        if len(args[0]) > 200:
            raise Refused("pattern too long")
        rx = re.compile(args[0])
        out = []
        for rel in tracked(inside(args[1]) if len(args) > 1 else "."):
            try:
                with open(os.path.join(root(), rel), "rb") as fh:
                    text = fh.read(MAX_BYTES).decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(text.split("\n"), 1):
                if rx.search(line):
                    out.append(f"{rel}:{n}: {line[:300]}")
                    if len(out) >= MAX_MATCHES:
                        return "\n".join([*out, "[more matches not shown]"])
        return "\n".join(out)
    fmt = "--format=%h %ad %an: %s"
    if op == "log":
        return git("log", fmt, "--date=short", "-n", "50", "--", inside(args[0]) if args else ".")
    if op == "log-S" and args:
        pick = "-S" + args[0]
        return git(
            "log",
            fmt,
            "--date=short",
            "-n",
            "50",
            pick,
            "--",
            inside(args[1]) if len(args) > 1 else ".",
        )
    if op == "show" and args:
        return git(
            "show",
            "--no-ext-diff",
            "--no-textconv",
            "--stat",
            "--patch",
            "--format=%h %ad %an%n%B",
            "--date=short",
            commit_id(args[0]),
            "--",
            inside(args[1]) if len(args) > 1 else ".",
        )
    if op == "blame" and args:
        return git("blame", "-s", "--", inside(args[0]))
    raise Refused(f"unknown request: {op}\n{__doc__}")


def masked(text: str) -> str:
    """Every line a denylist match touches, read the way the scanner reads it,
    is replaced. The denylist is the adjudicating machine's own."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import pii_scan

    deny, err, _ = pii_scan.load_denylist()
    if err is not None or deny is None:
        raise Refused("no denylist: refusing to show anything")
    sc = pii_scan.Scanner(deny, pii_scan.Allowlist(), set())
    lines = text.split("\n")[: MAX_LINES * 2]
    held = sc.withheld_lines(lines)
    return "\n".join(
        "[withheld: holds a denylisted value]" if i in held else ln for i, ln in enumerate(lines)
    )


def main(argv: list[str]) -> int:
    try:
        print(masked(cmd(argv)))
    except (Refused, ValueError, re.error, OSError, subprocess.TimeoutExpired) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
