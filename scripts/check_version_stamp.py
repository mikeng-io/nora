"""The build's grounding gate: refuse to build an image whose stamp disagrees.

Run INSIDE the image build, after `COPY nora/`, with the `NORA_VERSION` build
argument:

    python scripts/check_version_stamp.py "$NORA_VERSION"

It is a separate script from `gen_version_manifest.py` because the two run in
different worlds: the generator needs git and runs on the host, this needs
neither git nor the venv and runs in the container. It imports nothing from
`nora` for that reason — `nora/__init__.py` pulls in dependencies that are not
installed yet at this point in the build.

WHAT IT ENFORCES, and the ONE thing it deliberately does not:

1. **The stamp equals the build argument**, whenever a version is claimed. This
   is the whole point — "impossible to disagree": the tag the operator says they
   are building and the file the running code will read must be the same string,
   checked while the image is still being made rather than trusted afterwards.
2. **A claimed version must actually have a manifest.** Asking for V3 and
   shipping nothing to read is the same failure from the other side.
3. **An UNSTAMPED build is allowed.** Refusing an empty NORA_VERSION would
   break `docker compose build` and the plain `docker build --network=host` in
   README, both documented, with the requirement recorded only in a comment.
   That is the shape docker-compose.yml's `network: host` note exists to warn
   about.

   Refusing it would also buy nothing. The property being defended is «the reported
   version cannot disagree with the running code», and an image with no stamp
   REPORTS NOTHING: `version` answers `unavailable`. There is no disagreement
   to prevent. Forcing every config-change rebuild to invent a tag is workflow
   cost with no safety return, and would make the honest state (an untagged
   build) unreachable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MANIFEST = Path("nora/version.json")


def main(argv: list[str]) -> int:
    expected = (argv[1] if len(argv) > 1 else "").strip()
    if not expected:
        # An unstamped build, which is allowed — see the module docstring. But
        # it has to be unstamped for REAL: a manifest left in the build context
        # from an earlier stamped build would ride into the image and be read as
        # authoritative, so the image would report a version it is not running.
        # That is precisely the disagreement this gate exists to prevent, and
        # merely warning about it would leave the property depending on someone
        # reading the build log. Delete it instead — an image whose manifest is
        # absent answers `unavailable`, which is true.
        removed = ""
        if MANIFEST.exists():
            MANIFEST.unlink()
            removed = " (removed a stale nora/version.json from this build)"
        print(
            f"no NORA_VERSION: building UNSTAMPED{removed}. `version` will "
            "answer `unavailable`. `scripts/build-image.sh` stamps every build "
            "it can."
        )
        return 0
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
