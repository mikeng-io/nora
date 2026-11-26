"""Check every document against docs/engineering/documents.md.

`tests/architecture/test_documents.py` holds existing violations to
`tests/architecture/documents_baseline.json`, which only shrinks.

usage: python -m scripts.check_documents --write   # rewrite the baseline after a cleanup
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "architecture" / "documents_baseline.json"

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
    "README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "SECURITY.md",
    "CODE_OF_CONDUCT.md", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md",
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
VENDOR = re.compile(
    r"\b(?:Claude|Codex|Devin|Cursor|Gemini|Kimi|GPT-?\d|Sonnet|Opus|Haiku|Fable|"
    r"Antigravity|T3 Code|OpenAI|Anthropic|DeepSeek)\b"
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


def check(path: str, text: str) -> list[str]:
    """The single-file rules this document breaks, by name."""
    name = path.rsplit("/", 1)[-1]
    if "/" not in path:
        return [] if name in ROOT_DOCUMENTS else ["root-document"]
    found = []
    if _NAME.search(name) and not _ADR.match(path):
        found.append("numbered-or-dated-name")
    if path.startswith("docs/design/") and path.count("/") == 2 and name not in {
        "README.md", "decisions.md",
    }:
        found.append("design-root")
    if path.startswith("docs/design/") and len(text.splitlines()) > MAX_LINES:
        found.append("over-budget")
    if path in GENERATED:
        return found
    meta = frontmatter(text)
    if meta is None:
        return [*found, "frontmatter-missing"]
    kind = meta.get("class")
    if set(meta) - FIELDS:
        found.append("frontmatter-unknown-field")
    if kind not in CLASS_HOME:
        found.append("frontmatter-class")
    elif not path.startswith(CLASS_HOME[kind]):
        found.append("class-home")
    subject = meta.get("subject")
    if not isinstance(subject, str) or not subject.strip() or "\n" in subject.strip():
        found.append("frontmatter-subject")
    if not isinstance(meta.get("owns"), list) or not all(
        isinstance(e, str) or (isinstance(e, dict) and len(e) == 1) for e in meta["owns"]
    ):
        found.append("frontmatter-owns")
    if (kind == "design") != ("area" in meta):
        found.append("frontmatter-area")
    links = meta.get("links", [])
    if not isinstance(links, list) or not all(
        isinstance(l, dict) and set(l) == {"to", "rel"} and l["rel"] in RELS for l in links
    ):
        found.append("frontmatter-links")
    if "tracked_by" in meta and not isinstance(meta["tracked_by"], str):
        found.append("frontmatter-tracked-by")
    if "plain" in meta and (kind != "guide" or not isinstance(meta["plain"], dict)):
        found.append("frontmatter-plain")
    if kind == "engineering" and VENDOR.search(_FRONT.sub("", text)):
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
        for link in meta.get("links") or []:
            target = link.get("to") if isinstance(link, dict) else None
            if isinstance(target, str) and not exists(target) and target not in STAGED_TARGETS:
                result.setdefault(path, []).append("link-unresolved")
    for rule, table in (("subject-duplicate", subjects), ("owns-duplicate", owners)):
        for paths in table.values():
            if len(set(paths)) > 1:
                for path in set(paths):
                    result.setdefault(path, []).append(rule)
    return result


def documents() -> dict[str, str]:
    """Every tracked or new Markdown document at the root and under docs/."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],  # noqa: S607
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.split()
    return {
        path: (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for path in sorted(out)
        if ("/" not in path or path.startswith("docs/")) and (ROOT / path).is_file()
    }


def census() -> dict[str, list[str]]:
    files = documents()
    result = {path: found for path, text in files.items() if (found := check(path, text))}
    for path, found in tree_rules(files, lambda p: (ROOT / p).exists()).items():
        result.setdefault(path, []).extend(found)
    return {path: sorted(set(found)) for path, found in result.items()}


def main() -> int:
    if sys.argv[1:] != ["--write"]:
        print(__doc__)
        return 2
    now = census()
    BASELINE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(now)} documents with violations -> {BASELINE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
