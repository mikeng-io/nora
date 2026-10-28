"""Release every ordinary change reachable since the preceding stable tag.

Only generated version metadata is excluded, to avoid a release loop. Git read
failures remain errors; internal work qualifies even when hidden in the notes.
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
GENERATED = re.compile(
    r"^(?:chore\(master\): release \d+\.\d+\.\d+|"
    r"merge: prepare release v\d+\.\d+\.\d+ \(#\d+\))$"
)


def git(*args: str) -> str:
    return subprocess.check_output([shutil.which("git") or "/usr/bin/git", *args], text=True)  # noqa: S603


def releasable(base: str, head: str = "HEAD") -> Iterator[tuple[str, str]]:
    """Yield the complete reachable range, except generated release metadata.

    Dates and commit types never restrict eligibility. Include merges so a
    breaking declaration in a merge message cannot be hidden from the planner.
    """
    for row in git("log", "--format=%H %s", f"{base}..{head}").splitlines():
        sha, subject = row.split(" ", 1)
        if GENERATED.fullmatch(subject):
            paths = set(
                git(
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "--diff-merges=first-parent",
                    sha,
                ).splitlines()
            )
            message = git("show", "-s", "--format=%B", sha)
            if paths == METADATA_FILES and not re.search(r"^BREAKING[ -]CHANGE:", message, re.M):
                continue
        yield sha, subject


def eligible(base: str, head: str = "HEAD") -> bool:
    return any(True for _ in releasable(base, head))


if __name__ == "__main__":
    base = git("describe", "--tags", "--first-parent", "--abbrev=0", "--match", "v[0-9]*").strip()
    print(f"eligible={str(eligible(base)).lower()}")
