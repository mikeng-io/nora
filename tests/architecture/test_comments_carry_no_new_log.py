"""Python comments and docstrings gain no new log, and the old log only shrinks.

A date or an issue number in a comment is history the commit already holds
(`docs/CONVENTIONS.md` «Comments describe, git records»). Each tracked file is
held to its count in `log_marker_baseline.json`: above it fails (a new log
entry), below it fails too, so a cleanup lowers the baseline in the same
commit (`python scripts/log_markers.py --write`) and the progress is recorded.
A file absent from the baseline must carry none.
"""

from __future__ import annotations

import json

from scripts.log_markers import BASELINE, census, count


def test_detector_sees_a_log_marker() -> None:
    assert count('"""Raised after #191 (2026-09-01)."""\n# see #42\nx = 1\n') == 2
    assert count("# see #7\n# PR#123\n# issue#45\nx = 1\n") == 3
    assert count('COLOR = "#000000"  # black, 24-bit\nURL = "a/#123"\n') == 0


def test_log_markers_match_the_baseline() -> None:
    baseline: dict[str, int] = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = census()
    grew = {p: (baseline.get(p, 0), n) for p, n in now.items() if n > baseline.get(p, 0)}
    shrank = {p: (n, now.get(p, 0)) for p, n in baseline.items() if now.get(p, 0) < n}
    assert not grew, (
        "new dates or issue numbers in comments/docstrings (baseline, now); "
        f"put the history in the commit message instead: {grew}"
    )
    assert not shrank, (
        "log markers removed; record it with `python scripts/log_markers.py --write` "
        f"(baseline, now): {shrank}"
    )
