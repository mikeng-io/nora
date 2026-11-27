"""Root guides and agent-skill bodies state current rules, not a change log,
within a line budget: a new rule revises the section that owns it.

History lives in git, CHANGELOG.md, the ADRs and the decision index
(AGENTS.md «Design, documents and comments»). A dated note or an
issue/PR number in a guide is provenance written into an instruction.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOGS = {"CHANGELOG.md", "THIRD_PARTY_NOTICES.md"}
#: The process documents moved into docs/engineering/ stay instructions an agent reads.
MOVED = ["docs/engineering/orchestration.md", "docs/engineering/review.md"]
GUIDES = sorted(
    [p for p in ROOT.glob("*.md") if p.name not in LOGS]
    + list(ROOT.glob(".agents/skills/*/SKILL.md"))
    + [ROOT / p for p in MOVED]
)

# A bare ISO date; one inside a path or an anchor is part of a name.
DATE = re.compile(r"(?<![\w/#-])20\d\d-\d\d-\d\d(?![\w-])")
# An issue/PR reference; a backticked value such as a hex colour is not one.
REFERENCE = re.compile(r"(?<![\w/&`])#\d{2,5}\b(?!`)|\b(?:issue|PR) #?\d{2,5}\b", re.IGNORECASE)


def history_in(text: str) -> list[str]:
    return [m.group(0) for pattern in (DATE, REFERENCE) for m in pattern.finditer(text)]


def test_the_guides_are_found() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in GUIDES}
    assert {
        "AGENTS.md",
        "docs/engineering/orchestration.md",
        "GLOSSARY.md",
        ".agents/skills/nora-land/SKILL.md",
    } <= names


@pytest.mark.parametrize(
    "sample",
    [
        "Renamed 2026-08-06 (DEC-39).",
        "closed the same day (#272).",
        "see issue #187",
        "PR 454 showed",
    ],
)
def test_history_is_detected(sample: str) -> None:
    assert history_in(sample)


@pytest.mark.parametrize(
    "sample",
    [
        "`docs/security/2026-10-22-aikido-high-triage.md`",
        "47.md#decided-operator-2026-10-22",
        "No `#000`",
        "DEC-277",
    ],
)
def test_names_are_not_history(sample: str) -> None:
    assert not history_in(sample)


@pytest.mark.parametrize("path", GUIDES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_guide_carries_no_history(path: Path) -> None:
    assert history_in(path.read_text(encoding="utf-8")) == []


# AGENTS.md has a fixed budget; every other guide is capped at its size, so it
# can only be consolidated, never extended.
BUDGET = {
    "AGENTS.md": 250, "CLAUDE.md": 19, "CODE_OF_CONDUCT.md": 85,
    "CONTRIBUTING.md": 198, "GLOSSARY.md": 692, "README.md": 247, "SECURITY.md": 25,
    "docs/engineering/orchestration.md": 293, "docs/engineering/review.md": 57,
}
ROOT_GUIDES = [p for p in GUIDES if p.parent == ROOT or p.relative_to(ROOT).as_posix() in MOVED]


@pytest.mark.parametrize("path", ROOT_GUIDES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_guide_is_within_its_budget(path: Path) -> None:
    name = path.relative_to(ROOT).as_posix()
    assert name in BUDGET, f"{name} needs a line budget"
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= BUDGET[name], (
        f"{name} is {lines} lines, budget {BUDGET[name]}: revise or consolidate "
        "the section that owns the subject instead of adding to it"
    )
