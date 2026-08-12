"""Verify a release tag at HEAD exactly matches its archived rendered note."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed git command only
        ["git", *args],  # noqa: S607
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        tag_commit = _git(args.repo, "rev-list", "-n", "1", args.version).strip()
        head = _git(args.repo, "rev-parse", "HEAD").strip()
        note = _git(args.repo, "tag", "-l", "--format=%(contents)", args.version).strip()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    expected_path = args.repo / "release-fragments" / "released" / args.version / "NOTES.md"
    if not tag_commit or tag_commit != head:
        print(f"{args.version} must be an annotated tag at HEAD", file=sys.stderr)
        return 2
    if not expected_path.is_file():
        print(f"missing archived note: {expected_path}", file=sys.stderr)
        return 2
    expected = expected_path.read_text(encoding="utf-8").strip()
    if not note or note != expected:
        print(f"{args.version} annotation does not match {expected_path}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
