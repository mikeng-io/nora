"""Release shipped changes reachable since the preceding stable tag.

Non-runtime paths and generated metadata are excluded. Unknown paths qualify;
git read failures remain errors and explicit release declarations remain visible.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator

METADATA_FILES = frozenset(
    {
        ".release-please-manifest.json",
        "CHANGELOG.md",
        "nora/__init__.py",
        "pyproject.toml",
        "uv.lock",
    }
)
NON_RUNTIME_PREFIXES = (
    "docs/",
    "design/",
    "tests/",
    ".agents/",
    ".claude/",
    ".githooks/",
    ".github/",
    ".vscode/",
    ".idea/",
)
NON_RUNTIME_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "ARCHITECTURE.md",
        "CONTRIBUTING.md",
        "DESIGN.md",
        "ORCHESTRATION.md",
        "LICENSE",
        ".gitignore",
        ".editorconfig",
        ".pre-commit-config.yaml",
    }
)
DECLARATION = re.compile(
    r"^(?:[a-z]+(?:\([^)]*\))?!: |BREAKING[ -]CHANGE:|Release-Bump: minor[ \t]*$)", re.M
)
GENERATED = re.compile(
    r"^(?:chore\(master\): release \d+\.\d+\.\d+|"
    r"merge: prepare release v\d+\.\d+\.\d+ \(#\d+\))$"
)


def git(*args: str) -> str:
    return subprocess.check_output([shutil.which("git") or "/usr/bin/git", *args], text=True)  # noqa: S603


def releasable(base: str, head: str = "HEAD") -> Iterator[tuple[str, str]]:
    """Yield reachable shipped changes, preserving explicit release declarations.

    Dates and commit types never restrict eligibility. Include merges so a
    breaking declaration in a merge message cannot be hidden from the planner.
    """
    for row in git("log", "--format=%H %s", f"{base}..{head}").splitlines():
        sha, subject = row.split(" ", 1)
        paths = set(
            git(
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-z",
                "--no-renames",
                "-r",
                "--diff-merges=first-parent",
                sha,
            )
            .rstrip("\0")
            .split("\0")
        ) - {""}
        message = git("show", "-s", "--format=%B", sha)
        if DECLARATION.search(message):
            yield sha, subject
            continue
        if GENERATED.fullmatch(subject) and paths == METADATA_FILES:
            continue
        if paths and all(
            p in NON_RUNTIME_FILES or p.startswith(NON_RUNTIME_PREFIXES) for p in paths
        ):
            continue
        yield sha, subject


def eligible(base: str, head: str = "HEAD") -> bool:
    return any(True for _ in releasable(base, head))


if __name__ == "__main__":
    base = git("describe", "--tags", "--first-parent", "--abbrev=0", "--match", "v[0-9]*").strip()
    print(f"eligible={str(eligible(base)).lower()}")
