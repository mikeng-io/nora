"""An example TOML's comments are an operator reference, not a changelog.

A comment says what a section or key is and what an operator must know to set
it. History, dates, issue numbers, measurements and review stories belong to
git and the issue (`docs/maintainers/conventions-rationale.md` «Comments describe, git records»).
This gates the markers of a log, never the length: a length cap is satisfied
by deleting the comments an operator needs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_LOG_MARKER = re.compile(
    r"(?<![\w&])#\d{2,5}\b"  # an issue or PR number
    r"|\b20\d\d-\d\d(-\d\d)?\b"  # a date
    r"|\b[Mm]easur(ed|ement)\b"
    r"|\bBLOCKER\b"
    r"|\b[Ii]ssue\b"
    r"|\bPR\b"
    r"|\b[Rr]eview(er|ed)?\b"
)


def _comment_text(line: str) -> str:
    """The comment on a line, or "" — a `#` inside a quoted value is not one."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[i + 1 :]
    return ""


def _log_lines(text: str) -> list[str]:
    return [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if _LOG_MARKER.search(_comment_text(line))
    ]


def test_detector_sees_a_log_marker() -> None:
    assert _log_lines("# raised after #191, measured 2026-09-01\nkey = 1\n")
    assert not _log_lines('key = "a#123"  # seconds, at most 900\n')


def test_config_example_comments_carry_no_log(example_config_path: Path) -> None:
    assert _log_lines(example_config_path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", ["policy.example.toml", "identity.example.toml"])
def test_example_comments_carry_no_log(name: str) -> None:
    assert _log_lines((ROOT / name).read_text(encoding="utf-8")) == []
