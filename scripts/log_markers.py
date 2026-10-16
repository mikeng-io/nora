"""Count the markers of a log in Python comments and docstrings, per file.

A comment or docstring describes what the code is; the commit and the issue
are the log (`docs/CONVENTIONS.md` «Comments describe, git records»). The two
unambiguous markers of a log are a calendar date and an issue or PR number.
`tests/architecture/test_comments_carry_no_new_log.py` holds every tracked
file to the count in `tests/architecture/log_marker_baseline.json`, which may
only go down.

usage: python scripts/log_markers.py --write   # rewrite the baseline after a cleanup
"""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "architecture" / "log_marker_baseline.json"

LOG_MARKER = re.compile(
    r"\b20\d\d-\d\d-\d\d\b"  # a calendar date
    r"|(?<![\w&/])#\d{1,5}\b"  # an issue or PR number
    r"|\b(?:PR|[Ii]ssues?)\s*#\d+"  # one glued to its word
)


def count(source: str) -> int:
    """Comment tokens plus docstring lines that carry a log marker."""
    hits = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and LOG_MARKER.search(tok.string):
                hits += 1
    except (tokenize.TokenError, SyntaxError):
        return 0
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                hits += sum(bool(LOG_MARKER.search(line)) for line in doc.splitlines())
    return hits


def tracked_python_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],  # noqa: S607
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return sorted(p for p in out.splitlines() if (ROOT / p).is_file())


def census() -> dict[str, int]:
    counts = {}
    for path in tracked_python_files():
        n = count((ROOT / path).read_text(encoding="utf-8"))
        if n:
            counts[path] = n
    return counts


def main() -> int:
    if sys.argv[1:] != ["--write"]:
        print(__doc__)
        return 2
    counts = census()
    BASELINE.write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    total = sum(counts.values())
    print(f"{len(counts)} files, {total} marker lines -> {BASELINE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
