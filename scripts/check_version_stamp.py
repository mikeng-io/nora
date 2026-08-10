"""The build's grounding gate: refuse to build an image whose stamp disagrees.

Run INSIDE the image build, after `COPY nora/`, with the `NORA_VERSION` build
argument:

    python scripts/check_version_stamp.py "$NORA_VERSION"

It is a separate script from `gen_version_manifest.py` because the two run in
different worlds: the generator needs git and runs on the host, this needs
neither git nor the venv and runs in the container. It imports nothing from
`nora` for that reason — `nora/__init__.py` pulls in dependencies that are not
installed yet at this point in the build.

WHAT IT ENFORCES, and why each half is load-bearing:

1. **The manifest exists.** An unstamped image answers `unavailable` to every
   version question forever, which under R-5 looks exactly like a healthy Nora
   who was never asked. A build failure is the only loud version of that.
2. **The stamp equals the build argument.** This is the "impossible to disagree"
   half: the tag the operator says they are building and the file the running
   code will read must be the same string, checked against each other while the
   image is still being made rather than trusted afterwards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MANIFEST = Path("nora/version.json")


def main(argv: list[str]) -> int:
    expected = (argv[1] if len(argv) > 1 else "").strip()
    if not expected:
        print(
            "NORA_VERSION build argument is empty. Build with "
            "`scripts/build-image.sh V3`, which stamps nora/version.json from "
            "git and passes the same version here.",
            file=sys.stderr,
        )
        return 2
    if not MANIFEST.exists():
        print(
            f"{MANIFEST} is missing from the build context. It is generated on "
            "the host by scripts/gen_version_manifest.py (the container has no "
            "git and .dockerignore excludes .git/) — build with "
            "`scripts/build-image.sh`.",
            file=sys.stderr,
        )
        return 2
    try:
        stamped = json.loads(MANIFEST.read_text(encoding="utf-8")).get("version")
    except ValueError as exc:
        print(f"{MANIFEST} is not readable JSON: {exc}", file=sys.stderr)
        return 2
    if stamped != expected:
        print(
            f"version stamp disagrees with the build: {MANIFEST} says "
            f"{stamped!r}, --build-arg NORA_VERSION says {expected!r}. The "
            "image would report a version it is not running; regenerate the "
            "manifest for the version you mean.",
            file=sys.stderr,
        )
        return 2
    print(f"version stamp verified: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
