"""Check the documents a change touches against docs/engineering/documents.md.

Only changed files are judged, so an untouched legacy file is not, and touching one
means fixing it. The whole tree is still read for the cross-file rules.

usage: python -m scripts.check_documents --staged       # the pre-commit hook
       python -m scripts.check_documents --base <ref>   # CI: what a PR changed
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

CLASS_HOME = {
    "design": "docs/design/",
    "decision": "docs/design/decisions.md",
    "adr": "docs/adr/",
    "guide": "docs/guide/",
    "operations": "docs/operations/",
    "engineering": "docs/engineering/",
}
RELS = {"uses", "why", "governs", "derives"}
FIELDS = {"class", "subject", "owns", "area", "links", "tracked_by", "plain"}
ROOT_DOCUMENTS = {
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "THIRD_PARTY_NOTICES.md",
    "CHANGELOG.md",
}
#: Written by scripts/generate_docs_index.py, never by hand, so they carry no frontmatter.
GENERATED = {"docs/README.md", "docs/design/README.md", "docs/glossary.md"}
#: A link to a file a later calibration unit creates. Each entry names the unit that
#: removes it; U5 empties the list, after which an unresolved link always fails.
STAGED_TARGETS = {
    "docs/README.md": "U5",
    "docs/design/README.md": "U5",
    "docs/glossary.md": "U5",
    "docs/design/decisions.md": "U4",
    "docs/operations/deploy.md": "U5",
}
MAX_LINES = 300
#: Engineering policy is runtime-neutral: runtimes are in .agents/runtimes/,
#: lanes in .agents/lanes.md.
#: Unambiguous names match in any case; names that are also common words match capitalised.
VENDOR = re.compile(
    r"\b(?i:claude|codex|devin|gemini|kimi|gpt-?\d|sonnet|antigravity|t3 code|openai|"
    r"anthropic|deepseek)\b(?!\.md)|\b(?:Cursor|Opus|Haiku|Fable)\b(?!\.md)"
)
_NAME = re.compile(r"^\d|\d{4}-\d\d-\d\d")
_ADR = re.compile(r"^docs/adr/\d{4}-[a-z0-9-]+\.md$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def frontmatter(text: str) -> dict | None:
    """The YAML frontmatter, or None when the file has none or it does not parse."""
    match = _FRONT.match(text)
    if not match:
        return None
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return value if isinstance(value, dict) else None


def owned(meta: dict) -> list[str]:
    """`owns` entries are a term, or a single-key mapping of term to definition."""
    terms = []
    for entry in meta.get("owns") or []:
        terms.extend(entry if isinstance(entry, dict) else [entry])
    return [str(term) for term in terms]


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _entry(entry: object) -> bool:
    if isinstance(entry, dict):
        return len(entry) == 1 and all(_text(k) and _text(v) for k, v in entry.items())
    return _text(entry)


def check(path: str, text: str) -> list[str]:
    """The single-file rules this document breaks, by name."""
    name = path.rsplit("/", 1)[-1]
    if "/" not in path:
        return [] if name in ROOT_DOCUMENTS else ["root-document"]
    found = []
    if not name.endswith(".md"):
        return ["design-root"] if path.count("/") == 2 and path.startswith("docs/design/") else []
    if _NAME.search(name) and not _ADR.match(path):
        found.append("numbered-or-dated-name")
    if (
        path.startswith("docs/design/")
        and path.count("/") == 2
        and name
        not in {
            "README.md",
            "decisions.md",
        }
    ):
        found.append("design-root")
    # The budget is a bundle's: decisions.md and the generated map are not bundle files.
    in_bundle = path.startswith("docs/design/") and path.count("/") > 2
    if in_bundle and len(text.splitlines()) > MAX_LINES:
        found.append("over-budget")
    if path in GENERATED:
        return found
    meta = frontmatter(text)
    if meta is None:
        return [*found, "frontmatter-missing"]
    kind = meta.get("class") if isinstance(meta.get("class"), str) else None
    if set(meta) - FIELDS:
        found.append("frontmatter-unknown-field")
    if kind not in CLASS_HOME:
        found.append("frontmatter-class")
    elif not path.startswith(CLASS_HOME[kind]):
        found.append("class-home")
    subject = meta.get("subject")
    if not isinstance(subject, str) or not subject.strip() or "\n" in subject.strip():
        found.append("frontmatter-subject")
    if not isinstance(meta.get("owns"), list) or not all(_entry(e) for e in meta["owns"]):
        found.append("frontmatter-owns")
    if (kind == "design") != ("area" in meta) or ("area" in meta and not _text(meta["area"])):
        found.append("frontmatter-area")
    links = meta.get("links", [])
    if not isinstance(links, list) or not all(
        isinstance(link, dict) and set(link) == {"to", "rel"} and _text(link["to"])
        and isinstance(link["rel"], str) and link["rel"] in RELS
        for link in links
    ):
        found.append("frontmatter-links")
    if "tracked_by" in meta and not _text(meta["tracked_by"]):
        found.append("frontmatter-tracked-by")
    if "plain" in meta and (
        kind != "guide"
        or not isinstance(meta["plain"], dict)
        or not all(_text(k) and _text(v) for k, v in meta["plain"].items())
    ):
        found.append("frontmatter-plain")
    if kind == "engineering" and VENDOR.search(text):
        found.append("vendor-name")
    return found


def tree_rules(files: dict[str, str], exists) -> dict[str, list[str]]:
    """The rules that need the whole tree: unique ownership and resolving links."""
    result: dict[str, list[str]] = {}
    subjects: dict[str, list[str]] = {}
    owners: dict[str, list[str]] = {}
    for path, text in files.items():
        meta = frontmatter(text)
        if meta is None or path in GENERATED:
            continue
        if isinstance(meta.get("subject"), str):
            subjects.setdefault(meta["subject"].strip().lower(), []).append(path)
        if isinstance(meta.get("owns"), list):
            for term in owned(meta):
                owners.setdefault(term.strip().lower(), []).append(path)
        links = meta.get("links")
        for link in links if isinstance(links, list) else []:
            target = link.get("to") if isinstance(link, dict) else None
            if isinstance(target, str) and not exists(target) and target not in STAGED_TARGETS:
                result.setdefault(path, []).append("link-unresolved")
    for rule, table in (("subject-duplicate", subjects), ("owns-duplicate", owners)):
        for paths in table.values():
            if len(set(paths)) > 1:
                for path in set(paths):
                    result.setdefault(path, []).append(rule)
    return result


def resolves(target: str) -> bool:
    """A link names a file in the repository, by a relative path that stays inside it."""
    if target.startswith("/") or ".." in Path(target).parts:
        return False
    path = (ROOT / target).resolve()
    return path.is_file() and path.is_relative_to(ROOT.resolve())


def documents() -> dict[str, str]:
    """Root Markdown and every file under docs/, tracked or new. NUL-delimited, so
    no path is quoted or split by git."""
    out = (
        subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    chosen = (
        p for p in out if p and (p.startswith("docs/") or ("/" not in p and p.endswith(".md")))
    )
    # Every git entry is kept, a dangling symlink included, so the tree rules see it;
    # only a readable Markdown file contributes text.
    return {
        path: (ROOT / path).read_text(encoding="utf-8", errors="replace")
        if path.endswith(".md") and (ROOT / path).is_file()
        else ""
        for path in sorted(set(chosen))
    }


def _chosen(path: str) -> bool:
    return path.startswith("docs/") or ("/" not in path and path.endswith(".md"))


def changed(args: list[str]) -> list[str]:
    """Added, copied, modified or renamed documents: staged, or since a base ref."""
    rng = ["--cached"] if args == ["--staged"] else [args[1], "HEAD"]

    def names(kinds: str) -> set[str]:
        out = subprocess.run(
            ["git", "diff", "-z", "--name-only", f"--diff-filter={kinds}", *rng],  # noqa: S607
            cwd=ROOT, check=True, capture_output=True,
        ).stdout.decode("utf-8").split("\0")
        return {p for p in out if p and _chosen(p)}

    # A root document is judged only when it is new: editing one a later unit
    # moves into docs/ is not adding a root document.
    added = names("ACR")
    return sorted(p for p in names("ACMR") if "/" in p or p in added)


def staged_text(path: str) -> str:
    """A path's content as staged, so the hook judges what is being committed."""
    if not path.endswith(".md"):
        return ""
    done = subprocess.run(
        ["git", "show", f":{path}"], cwd=ROOT, capture_output=True,  # noqa: S607
    )
    return done.stdout.decode("utf-8", errors="replace") if done.returncode == 0 else ""


def violations(paths: list[str], read=None) -> dict[str, list[str]]:
    """The rules each changed path breaks; the rest of the tree is context only."""
    files = documents()
    if read is not None:
        files.update({path: read(path) for path in paths})
    result = {path: check(path, files.get(path, "")) for path in paths}
    markdown = {p: t for p, t in files.items() if p.endswith(".md")}
    for path, found in tree_rules(markdown, resolves).items():
        if path in result:
            result[path].extend(found)
    return {path: sorted(set(found)) for path, found in result.items() if found}


def main(argv: list[str]) -> int:
    if argv not in (["--staged"],) and not (len(argv) == 2 and argv[0] == "--base"):
        print(__doc__)
        return 2
    paths = changed(argv)
    found = violations(paths, staged_text if argv == ["--staged"] else None)
    for path, rules in found.items():
        print(f"{path}: {', '.join(rules)} (docs/engineering/documents.md)", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
