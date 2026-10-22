"""Plan the next release from the commits reachable from HEAD and not from the last tag.

release-please walks history in committer-date order and stops at the last release
commit, so a branch whose commits were written before that release and merged after
it is invisible to it: its `security` commits never reach a release PR.
`git log base..HEAD` is topological and has no such blind spot, and
`release_scope.releasable` already uses it to decide whether a release is due. This
script uses the same commits to write the release PR release-please would have
written, byte for byte in its format, so the release-please action can still tag
and publish it when it merges. No tag, a version that disagrees with the manifest,
or a file that does not hold the version being replaced is an error, never a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scripts.release_scope import git, releasable

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ".release-please-manifest.json"
PR_HEAD = "release-please--branches--master--components--nora"
CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<text>.+)$")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE: ", re.M)
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
INTRO = ":robot: I have created a release *beep* *boop*\n---\n\n\n"
OUTRO = (
    "\n---\nThis PR was generated with [Release Please]"
    "(https://github.com/googleapis/release-please). See [documentation]"
    "(https://github.com/googleapis/release-please#release-please)."
)


@dataclass(frozen=True)
class Entry:
    sha: str
    type: str
    scope: str
    text: str
    breaking: bool


def entries(base: str, head: str = "HEAD") -> list[Entry]:
    result = []
    for sha, subject in releasable(base, head):
        message = git("show", "-s", "--format=%B", sha)
        match = CONVENTIONAL.match(subject)
        if match:
            kind, scope, text = match["type"], match["scope"] or "", match["text"]
            breaking = bool(match["bang"]) or bool(BREAKING_FOOTER.search(message))
        else:  # git's own `Revert "<subject>"`
            kind, scope, text, breaking = "revert", "", subject, False
        result.append(Entry(sha, kind, scope, text, breaking))
    return result


def bump(current: str, found: list[Entry]) -> str:
    match = SEMVER.match(current)
    if not match:
        raise ValueError(f"manifest version {current!r} is not X.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    if any(e.breaking for e in found):
        return f"{major + 1}.0.0"
    if any(e.type == "feat" for e in found):
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def sections() -> list[tuple[str, str]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return [(s["type"], s["section"]) for s in config["changelog-sections"] if not s.get("hidden")]


def line(entry: Entry, repo: str) -> str:
    scope = f"**{entry.scope}:** " if entry.scope else ""
    link = f"([{entry.sha[:7]}](https://github.com/{repo}/commit/{entry.sha}))"
    return f"* {scope}{entry.text} {link}"


def render(previous: str, version: str, found: list[Entry], repo: str, today: str) -> str:
    """One `## [X.Y.Z]` CHANGELOG section, laid out as release-please lays it out."""
    compare = f"https://github.com/{repo}/compare/v{previous}...v{version}"
    out = f"## [{version}]({compare}) ({today})\n"
    groups = [("⚠ BREAKING CHANGES", [e for e in found if e.breaking])]
    groups += [(title, [e for e in found if e.type == kind]) for kind, title in sections()]
    for title, group in groups:
        if group:
            ordered = sorted(group, key=lambda e: (not e.scope, e.scope, e.text))
            out += f"\n\n### {title}\n\n" + "\n".join(line(e, repo) for e in ordered) + "\n"
    return out


def replace_once(path: Path, pattern: str, replacement: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(pattern, replacement, text, count=0, flags=re.M)
    if count != 1:
        raise ValueError(f"{what}: expected exactly one match in {path}, found {count}")
    path.write_text(new, encoding="utf-8")


def apply(root: Path, previous: str, version: str, section: str) -> None:
    """Write the version into every file release-please bumps, and the CHANGELOG."""
    old = re.escape(previous)
    (root / MANIFEST).write_text(json.dumps({".": version}, indent=2) + "\n", encoding="utf-8")
    replace_once(
        root / "nora/__init__.py",
        rf'^__version__ = "{old}"$',
        f'__version__ = "{version}"',
        "__version__",
    )
    replace_once(
        root / "pyproject.toml",
        rf'^version = "{old}"$',
        f'version = "{version}"',
        "project version",
    )
    replace_once(
        root / "uv.lock",
        rf'^(name = "nora"\nversion = ")({old})(")$',
        rf"\g<1>{version}\g<3>",
        "uv.lock nora version",
    )
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    head = "# Changelog\n\n"
    if not text.startswith(head):
        raise ValueError("CHANGELOG.md does not start with '# Changelog'")
    changelog.write_text(head + section + "\n" + text[len(head) :], encoding="utf-8")


def previous_release(root: Path) -> str:
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))["."]
    tag = git("describe", "--tags", "--first-parent", "--abbrev=0", "--match", "v[0-9]*").strip()
    if tag != f"v{manifest}":
        raise ValueError(f"latest release tag {tag} disagrees with the manifest version {manifest}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument(
        "--out", type=Path, required=True, help="directory for title.txt and body.md"
    )
    parser.add_argument("--today", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    args = parser.parse_args(argv)
    if not args.repo:
        raise SystemExit("release_plan: --repo or GITHUB_REPOSITORY is required")
    previous = previous_release(ROOT)
    found = entries(f"v{previous}")
    if not found:
        print("eligible=false")
        return 0
    version = bump(previous, found)
    section = render(previous, version, found, args.repo, args.today)
    apply(ROOT, previous, version, section)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "title.txt").write_text(f"chore(master): release {version}\n", encoding="utf-8")
    (args.out / "body.md").write_text(INTRO + section + OUTRO, encoding="utf-8")
    print("eligible=true")
    print(f"version={version}")
    print(f"head={PR_HEAD}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, subprocess.CalledProcessError, KeyError) as error:
        print(f"release_plan: {error}", file=sys.stderr)
        sys.exit(2)
