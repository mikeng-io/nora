"""Files, functions, comment blocks and internal docstrings stay within their
limits; recorded violations may shrink but never grow."""

from __future__ import annotations

import json

from scripts.code_size import (
    BASELINE,
    MAX_FILE_LINES,
    MAX_FUNCTION_LINES,
    census,
    compare,
    measure,
)


def test_detector_sees_each_violation() -> None:
    long_body = "\n".join(f"    x{i} = {i}" for i in range(MAX_FUNCTION_LINES))
    source = (
        '"""Module."""\n'
        "# one\n# two\n# three\n# four\n"
        "def _helper():\n"
        '    """First.\n\n    Second."""\n'
        f"{long_body}\n"
        "def fine():\n"
        '    """A public docstring may run long.\n\n    Like this."""\n'
        "    # a three-line block\n    # is allowed\n    # here\n"
        "    return 1\n"
    )
    found = measure(source)
    assert found["comment_blocks"] == 1
    assert found["internal_docstrings"] == 1
    assert set(found["functions"]) == {"_helper"}  # type: ignore[arg-type]
    assert measure("x = 1\n" * (MAX_FILE_LINES + 1))["file_lines"] == MAX_FILE_LINES + 1
    assert measure("x = 1\n") == {}


def test_compare_fails_growth_and_stale_entries() -> None:
    base = {"a.py": {"file_lines": 900, "functions": {"f": 200}}}
    assert compare(base, base) == ([], [])
    assert compare(base, {"a.py": {"file_lines": 850, "functions": {"f": 150}}}) == ([], [])
    grew, _ = compare(base, {"a.py": {"file_lines": 901, "functions": {"f": 200, "g": 130}}})
    assert grew == ["a.py file_lines: 900 -> 901", "a.py::g lines: 0 -> 130"]
    _, shrank = compare(base, {"a.py": {"file_lines": 900}})
    assert shrank == ["a.py::f"]


def test_code_matches_the_baseline() -> None:
    grew, shrank = compare(json.loads(BASELINE.read_text(encoding="utf-8")), census())
    assert not grew, (
        "code grew past its size limit; split the file or function, or shorten "
        f"the comment or docstring: {grew}"
    )
    assert not shrank, (
        f"violations removed; record it with `python scripts/code_size.py --write`: {shrank}"
    )
