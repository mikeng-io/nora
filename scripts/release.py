#!/usr/bin/env python3
"""Create the next manual Nora release, then build and restart it.

The operator decides when to run this command.  The committed release record is
``docs/releases/V<N>.md``; its English summary stays in Git while its
``## Group message`` section becomes the annotated tag message Nora relays.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"V(\d+)$")


def run(*args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed release-script commands only
        args, cwd=ROOT, text=True, capture_output=True, check=False
    )
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or " ".join(args))
    return done.stdout


def next_version() -> str:
    numbers = [
        int(match.group(1))
        for tag in run("git", "tag", "--list", "V*").splitlines()
        if (match := VERSION.fullmatch(tag)) is not None
    ]
    highest = max(numbers, default=0)
    return f"V{highest + 1}"


def group_message(path: Path, version: str) -> str:
    if not path.exists():
        raise ValueError(f"release note is missing: {path.relative_to(ROOT)}")
    text = path.read_text(encoding="utf-8")
    if not text.startswith(f"# {version}") or "## Summary\n" not in text:
        raise ValueError(f"release note must start with '# {version}' and include '## Summary'")
    match = re.search(r"^## Group message\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise ValueError("release note needs a non-empty '## Group message' section")
    return match.group(1).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-deploy", action="store_true", help="tag only; used by tests")
    args = parser.parse_args(argv)
    try:
        if run("git", "status", "--porcelain"):
            raise ValueError("refusing to release a dirty working tree")
        version = next_version()
        note = group_message(ROOT / "docs" / "releases" / f"{version}.md", version)
        run("git", "tag", "-a", version, "-m", note)
        if not args.skip_deploy:
            subprocess.run(  # noqa: S603 - fixed local release builder only
                [str(ROOT / "scripts" / "build-release-image.sh"), version],
                cwd=ROOT,
                check=True,
            )
            subprocess.run(
                ["docker", "compose", "up", "-d", "--no-build", "nora", "presence"],  # noqa: S607
                cwd=ROOT,
                check=True,
            )
        print(f"released {version}")
        return 0
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
