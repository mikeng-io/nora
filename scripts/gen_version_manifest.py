"""Stamp `nora/version.json` — the build-time version manifest.

Run on the HOST, immediately before `docker build`, by `scripts/build-image.sh`:

    python3 scripts/gen_version_manifest.py            # git describe decides
    python3 scripts/gen_version_manifest.py --version v6.1.0

WHY THE HOST AND NOT THE IMAGE. `.dockerignore` excludes `.git/`, and it should:
the build context has no history and the container has no git binary, so nothing
inside the image can derive a version. The manifest is therefore generated from
the working tree being built and copied in by the same `COPY nora/` that copies
the code — one filesystem snapshot, one version.

THE SCHEME (DEC-314). Releases are cut on GitHub by release-please, which tags
`vX.Y.Z` from the Conventional Commits merged since the last release and writes
the version's section of `CHANGELOG.md`. A build is stamped with
`git describe --tags --match 'v[0-9]*'`: `v6.1.0` when HEAD is the release
commit, `v6.1.0-3-gabc1234` when it is three commits past it. Both are honest
names for what the image holds. The `V1`…`V5` tags of the manual scheme stay as
history and are listed after the semver ones.

WHAT IT WRITES.

    {"version": "v6.1.0",                  # what this build IS
     "commit": "<sha>", "built_at": "...",
     "versions": [                         # newest first
       {"version": "v6.1.0", "released": "YYYY-MM-DD", "commit": "<sha>",
        "notes": "<this version's CHANGELOG.md section>",
        "changes": ["<commit subject>", ...]}]}

`notes` is the version's `CHANGELOG.md` section (what release-please published
as the GitHub Release body), or an old `V<n>` tag's annotation. `changes` is the
commit subjects in the version's range, for «what was in v6.1.0».

ONE REFUSAL, because a wrong stamp is worse than no stamp: a dirty working
tree, unless `--allow-dirty` is passed. A dirty tree means the image contains
code no commit describes, so the recorded `commit` would be a claim about an
artifact that never existed. «Dirty» is any change to a tracked file, or an
untracked file under a directory the image copies (`IMAGE_DIRS`). An untracked
file anywhere else never reaches the image, and the deploy host's checkout
always holds some (backups, `.uacp/`). A version outside the scheme is refused
too: nothing else in the system can match on it.

Standard library only, so the deploy host runs it with `python3`, where `uv`
is not on a non-interactive shell's PATH.
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
CHANGELOG = ROOT / "CHANGELOG.md"

#: A release tag, and what a build may be stamped as: the tag itself, or
#: `git describe`'s "N commits past it" form.
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(-\d+-g[0-9a-f]+)?$")
#: The manual scheme's tags, V1…V5, kept as history.
LEGACY_TAG_RE = re.compile(r"^V\d+$")

# How many commit subjects a version carries. A tool result is re-sent on every
# subsequent model call, so an unbounded changelog is paid over and over
# (the fetch-token lesson, DEC-73). Twenty is a readable "what was in it".
MAX_SUBJECTS = 20
# The same budget for a version's notes: a CHANGELOG section can run long.
MAX_NOTES_CHARS = 2000

# The directories the `Dockerfile` COPYs into the image. An untracked file
# under one of them is in the image and no commit describes it; an untracked
# file outside them is not in the image. Pinned against the Dockerfile's COPY
# lines by `tests/test_version_stamp_build.py`.
IMAGE_DIRS = ("nora", "evals", "scripts")

# release-please's version heading: `## [6.1.0](<compare url>) (YYYY-MM-DD)`,
# or `## 6.1.0 (…)` for a first release with no previous tag to compare to.
_HEADING_RE = re.compile(r"^##\s+\[?(\d+\.\d+\.\d+)\]?", re.M)
# The links release-please appends to every line: a commit's `([abc1234](url))`
# and an issue's `[#N](url)`. Kept as the bare `#N`; the urls are tokens the
# model re-reads on every call and can do nothing with.
_COMMIT_LINK_RE = re.compile(r"\s*\(\[[0-9a-f]{7,40}\]\([^)]*\)\)")
_ISSUE_LINK_RE = re.compile(r"\[(#\d+)\]\([^)]*\)")


def _git(*args: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, repo-local
        ["git", *args],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def _dirty() -> str:
    """What makes this tree unfit to stamp: any tracked change, or an
    untracked file under `IMAGE_DIRS`. Empty when clean."""
    tracked = _git("status", "--porcelain", "--untracked-files=no")
    in_image = _git("status", "--porcelain", "--untracked-files=all", "--", *IMAGE_DIRS)
    return tracked or in_image


def _tags() -> list[str]:
    """Every release tag newest first, then the legacy `V<n>` tags newest first.

    `--sort=-v:refname` orders v6.10.0 after v6.9.0 and V10 after V9, which
    plain lexicographic sorting gets wrong. `git tag --list` patterns are
    case-sensitive, so `v*` and `V*` never pick up each other's tags.
    """
    semver = _git("tag", "--list", "v*", "--sort=-v:refname").splitlines()
    legacy = _git("tag", "--list", "V*", "--sort=-v:refname").splitlines()
    return [t.strip() for t in semver if RELEASE_TAG_RE.match(t.strip())] + [
        t.strip() for t in legacy if LEGACY_TAG_RE.match(t.strip())
    ]


def changelog_sections(text: str) -> dict[str, str]:
    """`CHANGELOG.md`'s version sections, keyed `v<X.Y.Z>`, links stripped."""
    sections: dict[str, str] = {}
    headings = list(_HEADING_RE.finditer(text))
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        body = text[heading.end():end].split("\n", 1)[-1]
        body = _ISSUE_LINK_RE.sub(r"\1", _COMMIT_LINK_RE.sub("", body)).strip()
        sections[f"v{heading.group(1)}"] = body[:MAX_NOTES_CHARS]
    return sections


def _annotation(tag: str) -> str:
    """An annotated tag's message, or empty. `%(contents)` of a LIGHTWEIGHT tag
    is the commit's message, which is not a note anybody wrote about a version,
    so it is read only when the tag is a tag object."""
    if _git("cat-file", "-t", tag) != "tag":
        return ""
    return _git("tag", "-l", "--format=%(contents)", tag).strip()


def _entry(tag: str, previous: str | None, notes: dict[str, str]) -> dict[str, object]:
    """One version's record: its notes, its commit, its date, its subjects.

    `previous` is the NEXT-OLDER tag; the subject range is `previous..tag`, or
    everything up to `tag` for the first version.
    """
    span = f"{previous}..{tag}" if previous else tag
    subjects = [s for s in _git("log", "--format=%s", span).splitlines() if s]
    return {
        "version": tag,
        "released": _git("log", "-1", "--format=%cs", tag),
        "commit": _git("rev-list", "-n", "1", tag),
        "notes": (notes.get(tag) or _annotation(tag))[:MAX_NOTES_CHARS],
        "changes": subjects[:MAX_SUBJECTS],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default="",
        help="the version this build IS (v6.1.0, v6.1.0-3-gabc1234). Defaults "
             "to `git describe --tags --match 'v[0-9]*'`.",
    )
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="stamp anyway when the working tree has uncommitted changes",
    )
    parser.add_argument("--out", default=str(MANIFEST))
    args = parser.parse_args(argv)

    if _dirty() and not args.allow_dirty:
        print(
            "refusing to stamp a dirty working tree: the image would contain "
            "code no commit describes, so the recorded commit would name an "
            "artifact that never existed. Commit, or pass --allow-dirty.",
            file=sys.stderr,
        )
        return 2

    version = args.version or _git("describe", "--tags", "--match", "v[0-9]*")
    if not VERSION_RE.match(version):
        print(
            f"refusing to stamp version {version!r}: a build is stamped vX.Y.Z or "
            "vX.Y.Z-N-g<sha>. No release tag is reachable from HEAD yet, or "
            "--version was mistyped.",
            file=sys.stderr,
        )
        return 2

    notes = (changelog_sections(CHANGELOG.read_text(encoding="utf-8"))
             if CHANGELOG.exists() else {})
    tags = _tags()
    entries = [
        _entry(tag, tags[i + 1] if i + 1 < len(tags) else None, notes)
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
