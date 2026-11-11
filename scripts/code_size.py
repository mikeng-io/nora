"""Measure Python size limits per tracked file: file length, function length,
comment-block length and internal-docstring length.

`tests/architecture/test_code_stays_within_size_limits.py` holds every
violation to `tests/architecture/code_size_baseline.json`: a new violation or
a recorded one that grew fails, and an entry no longer violating fails until
the baseline is rewritten, so the baseline only shrinks.

usage: python scripts/code_size.py --write   # rewrite the baseline after a cleanup
"""

from __future__ import annotations

import ast
import io
import json
import sys
import tokenize

from scripts.log_markers import ROOT, tracked_python_files

BASELINE = ROOT / "tests" / "architecture" / "code_size_baseline.json"

MAX_FILE_LINES = 800
MAX_FUNCTION_LINES = 120
MAX_COMMENT_BLOCK_LINES = 3
MAX_INTERNAL_DOCSTRING_LINES = 1


def _comment_blocks(source: str) -> int:
    """Runs of consecutive full-line comments longer than the limit."""
    lines = source.splitlines()
    blocks, run, prev = 0, 0, -2
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type != tokenize.COMMENT:
                continue
            row = tok.start[0]
            shebang = row == 1 and tok.string.startswith("#!")
            if shebang or not lines[row - 1].lstrip().startswith("#"):
                continue
            run = run + 1 if row == prev + 1 else 1
            prev = row
            if run == MAX_COMMENT_BLOCK_LINES + 1:
                blocks += 1
    except (tokenize.TokenError, SyntaxError):
        return 0
    return blocks


def measure(source: str) -> dict[str, object]:
    """One file's violations; empty when it is within every limit."""
    out: dict[str, object] = {}
    n = len(source.splitlines())
    if n > MAX_FILE_LINES:
        out["file_lines"] = n
    if blocks := _comment_blocks(source):
        out["comment_blocks"] = blocks
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    functions: dict[str, int] = {}
    docstrings = 0

    def walk(node: ast.AST, prefix: str) -> None:
        nonlocal docstrings
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    length = child.end_lineno - child.lineno + 1  # type: ignore[operator]
                    if length > MAX_FUNCTION_LINES:
                        functions[name] = max(functions.get(name, 0), length)
                    doc = ast.get_docstring(child, clean=True)
                    internal = child.name.startswith("_") and not child.name.endswith("__")
                    if internal and doc and len(doc.splitlines()) > MAX_INTERNAL_DOCSTRING_LINES:
                        docstrings += 1
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    if functions:
        out["functions"] = dict(sorted(functions.items()))
    if docstrings:
        out["internal_docstrings"] = docstrings
    return out


def census() -> dict[str, dict[str, object]]:
    result = {}
    for path in tracked_python_files():
        found = measure((ROOT / path).read_text(encoding="utf-8"))
        if found:
            result[path] = found
    return result


def compare(baseline: dict, now: dict) -> tuple[list[str], list[str]]:
    """(grew, shrank): violations above their baseline, and entries to drop."""
    grew, shrank = [], []
    for path in sorted(set(baseline) | set(now)):
        was, cur = baseline.get(path, {}), now.get(path, {})
        for key in ("file_lines", "comment_blocks", "internal_docstrings"):
            a, b = was.get(key, 0), cur.get(key, 0)
            if b > a:
                grew.append(f"{path} {key}: {a} -> {b}")
            elif b < a and b == 0:
                shrank.append(f"{path} {key}")
        wf, cf = was.get("functions", {}), cur.get("functions", {})
        for fn in sorted(set(wf) | set(cf)):
            a, b = wf.get(fn, 0), cf.get(fn, 0)
            if b > a:
                grew.append(f"{path}::{fn} lines: {a} -> {b}")
            elif b == 0:
                shrank.append(f"{path}::{fn}")
    return grew, shrank


def main() -> int:
    if sys.argv[1:] != ["--write"]:
        print(__doc__)
        return 2
    now = census()
    BASELINE.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(now)} files with violations -> {BASELINE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
