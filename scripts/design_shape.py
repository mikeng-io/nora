"""Check that every file under design/ has the shape of a design document.

`tests/architecture/test_design_is_design.py` holds existing violations to
`tests/architecture/design_shape_baseline.json`, which only shrinks.

usage: python -m scripts.design_shape --write   # rewrite the baseline after a cleanup
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from scripts.log_markers import ROOT

BASELINE = ROOT / "tests" / "architecture" / "design_shape_baseline.json"

MAX_README_LINES = 150
MAX_FILE_LINES = 300
NOT_DESIGN = {"decision", "decisions", "review", "reviews", "roadmap", "items", "draft", "plan",
              "plans", "migration"}

_NAME = re.compile(r"^\d|\d{4}-\d\d-\d\d")
_DATE = re.compile(r"(?<![\w/#-])20\d\d-\d\d-\d\d(?![\w-])")
_BANNER = re.compile(r"\b(?:DEPRECATED|SUPERSEDED|REVERSED)\b|^supersedes:", re.M)


def check(path: str, text: str) -> list[str]:
    """The shape rules this design file breaks, by name."""
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0].lower()
    found = []
    if _NAME.search(name):
        found.append("numbered-or-dated-name")
    words = set(re.split(r"[-_.]", stem))
    if not name.endswith(".md") or words & NOT_DESIGN or stem.startswith("_"):
        found.append("not-a-design-file")
    if _DATE.search(text):
        found.append("date")
    if _BANNER.search(text):
        found.append("history-marker")
    limit = MAX_README_LINES if name == "README.md" else MAX_FILE_LINES
    if len(text.splitlines()) > limit:
        found.append("over-budget")
    return found


def census() -> dict[str, list[str]]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "design"],  # noqa: S607
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.split()
    result = {}
    for path in sorted(out):
        file = ROOT / path
        if not file.is_file():
            continue
        if found := check(path, file.read_text(encoding="utf-8", errors="replace")):
            result[path] = found
    return result


def main() -> int:
    if sys.argv[1:] != ["--write"]:
        print(__doc__)
        return 2
    now = census()
    BASELINE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(now)} design files with violations -> {BASELINE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
