"""Require a release fragment for a branch with changes against its base."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = "release-fragments/unreleased"
RENDERER = ROOT / "scripts" / "render_release_notes.py"


def git(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, repo-local git only
        ["git", *args],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="the branch or commit being merged into")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        base = git(args.repo, "merge-base", args.base, "HEAD")[0]
        changed = git(args.repo, "diff", "--name-only", f"{base}...HEAD")
        statuses = git(args.repo, "diff", "--name-status", f"{base}...HEAD")
    except (RuntimeError, IndexError) as exc:
        print(f"cannot determine release-fragment scope: {exc}", file=sys.stderr)
        return 2
    if not changed:
        return 0
    fragments = [
        fields[1]
        for line in statuses
        if (fields := line.split("\t")) and fields[0] == "A" and len(fields) == 2
        and fields[1].startswith(f"{FRAGMENTS}/") and fields[1].endswith(".json")
    ]
    if not fragments:
        print(
            "changed branch has no release fragment; add release-fragments/unreleased/<name>.json "
            "(use type 'none' with a reason for internal-only work)",
            file=sys.stderr,
        )
        return 2
    validation = subprocess.run(  # noqa: S603 - fixed local renderer command
        [sys.executable, str(RENDERER), "--dir", str(args.repo / FRAGMENTS)],
        capture_output=True,
        text=True,
        check=False,
    )
    if validation.returncode:
        print(validation.stderr, end="", file=sys.stderr)
        return validation.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
