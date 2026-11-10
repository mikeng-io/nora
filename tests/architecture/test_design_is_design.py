"""Files under design/ are design documents: named by subject, no history
markers or dates, within budget. Recorded violations only shrink."""

from __future__ import annotations

import json

from scripts.design_shape import BASELINE, MAX_FILE_LINES, census, check


def test_detector_sees_each_violation() -> None:
    assert check("design/a/notes.md", "A note is a claim.\n") == []
    assert check("design/a/README.md", "x\n" * 151) == ["over-budget"]
    assert check("design/a/notes.md", "x\n" * (MAX_FILE_LINES + 1)) == ["over-budget"]
    assert check("design/a/03-decisions.md", "") == ["numbered-or-dated-name", "not-a-design-file"]
    assert check("design/a/_index.yaml", "") == ["not-a-design-file"]
    assert check("design/a/x.md", "> **DEPRECATED** in part\n") == ["history-marker"]
    assert check("design/a/x.md", "---\nsupersedes: other\n---\n") == ["history-marker"]
    assert check("design/a/x.md", "Decided 2026-09-07.\n") == ["date"]


def test_design_matches_the_baseline() -> None:
    baseline: dict[str, list[str]] = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = census()
    new = {p: sorted(set(v) - set(baseline.get(p, []))) for p, v in now.items()}
    new = {p: v for p, v in new.items() if v}
    gone = {p: sorted(set(v) - set(now.get(p, []))) for p, v in baseline.items()}
    gone = {p: v for p, v in gone.items() if v}
    assert not new, (
        "design files break the design shape (nora-design): revise the owning "
        f"document instead of adding numbered, dated or history-marked text: {new}"
    )
    assert not gone, (
        f"violations fixed; record it with `python -m scripts.design_shape --write`: {gone}"
    )
