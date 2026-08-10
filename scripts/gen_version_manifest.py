"""Stamp `nora/version.json` — the build-time version manifest.

Run on the HOST, immediately before `docker build`, by `scripts/build-image.sh`:

    uv run python scripts/gen_version_manifest.py --version V3

WHY THE HOST AND NOT THE IMAGE. `.dockerignore` excludes `.git/`, and it should:
the build context has no history and the container has no git binary, so nothing
inside the image can derive a version. The manifest is therefore generated from
the working tree being built and copied in by the same `COPY nora/` that copies
the code — one filesystem snapshot, one version.

WHAT IT WRITES.

    {"version": "V3",                      # what this build IS
     "commit": "<sha>", "built_at": "...",
     "versions": [                         # newest first
       {"version": "V3", "released": "2026-08-11", "commit": "<sha>",
        "notes": "<the tag annotation — the operator's own words>",
        "changes": ["<commit subject>", ...]}]}

`notes` is the ANNOTATION of the tag (`git tag -m …`, which is also what a
GitHub release body carries) and is the only field the startup announcement
relays. `changes` is commit subjects, which exist to answer «what was in V2» and
are deliberately NOT announcement copy: an auto-generated subject list is not a
note anybody wrote.

TWO REFUSALS, both because a wrong stamp is worse than no stamp:
  * a version that is not `V<n>` (the scheme is plain V1/V2/V3 — no semver);
  * a dirty working tree, unless `--allow-dirty` is passed. A dirty tree means
    the image contains code no commit describes, so the recorded `commit` would
    be a claim about an artifact that never existed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "nora" / "version.json"

# The scheme, as a gate. Plain V1, V2, V3 — an operator who types `v3.1` or
# `3` gets a refusal rather than a manifest nothing else in the system can
# match against.
VERSION_RE = re.compile(r"^V\d+$")

# How many commit subjects a version carries. A tool result is re-sent on every
# subsequent model call, so an unbounded changelog is paid over and over
# (the fetch-token lesson, DEC-73). Twenty is a readable "what was in it".
MAX_SUBJECTS = 20


def _git(*args: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, repo-local
        ["git", *args],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def _tags() -> list[str]:
    """Every `V<n>` tag, newest first.

    `--sort=-v:refname` orders V10 after V9 rather than between V1 and V2, which
    plain lexicographic sorting gets wrong the day a tenth version ships.
    """
    listed = _git("tag", "--list", "V*", "--sort=-v:refname").splitlines()
    return [t.strip() for t in listed if VERSION_RE.match(t.strip())]


def _entry(tag: str, previous: str | None) -> dict[str, object]:
    """One version's record: its annotation, its commit, its date, its subjects.

    `previous` is the NEXT-OLDER tag; the subject range is `previous..tag`, or
    everything up to `tag` for the first version. `%(contents)` is empty for a
    lightweight tag, which is honest — a version tagged without a message has no
    note, and the announcement path treats an empty note as "nothing to say"
    rather than inventing one from the subjects.
    """
    span = f"{previous}..{tag}" if previous else tag
    subjects = [s for s in _git("log", "--format=%s", span).splitlines() if s]
    return {
        "version": tag,
        "released": _git("log", "-1", "--format=%cs", tag),
        "commit": _git("rev-list", "-n", "1", tag),
        "notes": _git("tag", "-l", "--format=%(contents)", tag).strip(),
        "changes": subjects[:MAX_SUBJECTS],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default="",
        help="the version this build IS (V1, V2, …). Defaults to the most "
             "recent V-tag reachable from HEAD.",
    )
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="stamp anyway when the working tree has uncommitted changes",
    )
    parser.add_argument("--out", default=str(MANIFEST))
    args = parser.parse_args(argv)

    if _git("status", "--porcelain") and not args.allow_dirty:
        print(
            "refusing to stamp a dirty working tree: the image would contain "
            "code no commit describes, so the recorded commit would name an "
            "artifact that never existed. Commit, or pass --allow-dirty.",
            file=sys.stderr,
        )
        return 2

    version = args.version or _git("describe", "--tags", "--abbrev=0", "--match", "V*")
    if not VERSION_RE.match(version):
        print(
            f"refusing to stamp version {version!r}: the scheme is plain V1/V2/V3. "
            "Pass --version, or tag the commit you are building.",
            file=sys.stderr,
        )
        return 2

    tags = _tags()
    entries = [
        _entry(tag, tags[i + 1] if i + 1 < len(tags) else None)
        for i, tag in enumerate(tags)
    ]
    manifest = {
        "version": version,
        "commit": _git("rev-parse", "HEAD"),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "versions": entries,
    }
    out = Path(args.out)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"stamped {out} as {version} ({len(entries)} version(s) recorded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
