"""The shipped `navigation` skill — that it loads, and that it stays a skill.

These guard the discipline design/17 asks of a skill: it must be about
WAYFINDING, not about any one trip (a skill that names a scenario is a scenario
that fails its neighbour), it must say when to load AND when not to, and it must
stay small — most of what looks like skill content belongs a layer up (doctrine)
or down (tools).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nora.skills.loader import SkillStore

SKILLS_DIR = Path(__file__).resolve().parents[1] / "nora" / "skills"


@pytest.fixture
def navigation():
    skill = SkillStore(SKILLS_DIR).resolve("navigation")
    assert skill is not None, "the navigation skill must load from nora/skills/"
    return skill


def test_the_navigation_skill_loads_and_is_well_formed(navigation):
    assert navigation.name == "navigation"
    assert navigation.description.strip()
    assert navigation.instructions.strip()


def test_the_description_says_both_when_to_load_and_when_not_to(navigation):
    """A skill costs a turn to activate (design/12 §4), so it must earn it — a
    bare live check does NOT need it, a plan does. The description carries both."""
    desc = navigation.description
    assert "PLAN" in desc or "plan" in desc
    # The negative half — the load-trigger names something it is NOT for.
    assert "Not for" in desc or "not for" in desc


def test_the_skill_names_no_scenario(navigation):
    """design/17 (operator-insisted): the skill handles the dinner run, the
    airport run and the shopping trip by being about wayfinding, not about any
    one of them. Naming a scenario is the failure mode."""
    haystack = (navigation.description + "\n" + navigation.instructions).lower()
    for scenario in ("dinner", "airport", "shopping", "晚飯", "機場", "食飯", "買嘢"):
        assert scenario not in haystack, f"skill names a scenario: {scenario!r}"


def test_the_skill_stays_small(navigation):
    """Most of what looks like skill content belongs a layer up or down; what is
    left is genuinely small. A large skill is stealing a lower layer's job."""
    lines = [ln for ln in navigation.instructions.splitlines() if ln.strip()]
    assert len(lines) <= 30, f"navigation skill body has {len(lines)} non-blank lines"
