"""Archive unreleased fragments, commit them, and create the matching version tag."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from render_release_notes import DEFAULT_DIR, load_fragments, render

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed git command only
        ["git", *args],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="plain V<n> version tag")
    args = parser.parse_args(argv)
    if not args.version.startswith("V") or not args.version[1:].isdigit():
        parser.error("version must be V<n>")
    try:
        if _git("status", "--porcelain"):
            raise ValueError("working tree must be clean before preparing a release")
        if _git("tag", "-l", args.version):
            raise ValueError(f"tag already exists: {args.version}")
        fragments = load_fragments(DEFAULT_DIR)
        notes = render(fragments)
        if not notes:
            raise ValueError("release has no user-visible fragments to announce")
        destination = ROOT / "release-fragments" / "released" / args.version
        if destination.exists():
            raise ValueError(f"archive already exists: {destination}")
        destination.mkdir(parents=True)
        for path in sorted(DEFAULT_DIR.glob("*.json")):
            shutil.move(str(path), destination / path.name)
        (destination / "NOTES.md").write_text(notes, encoding="utf-8")
        _git("add", "release-fragments")
        _git("commit", "-m", f"chore(release): prepare {args.version}")
        _git("tag", "-a", args.version, "-F", str(destination / "NOTES.md"))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"prepared {args.version}; build with scripts/build-image.sh {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
