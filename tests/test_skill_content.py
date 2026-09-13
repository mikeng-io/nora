"""Every shipped skill — that it loads, and that it stays a skill.

These guard the discipline design/17 asks of a skill: it must be about a
DOMAIN, not about any one scenario (a skill that names a scenario is a scenario
that fails its neighbour), it must say when to load AND when not to, and it must
stay small — most of what looks like skill content belongs a layer up (doctrine)
or down (tools).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nora.skills.loader import SkillStore

SKILLS_DIR = Path(__file__).resolve().parents[1] / "nora" / "skills"

# Per-skill scenario lists: the concrete trips/asks a skill must NOT name,
# because naming one is the failure mode (design/17, operator-insisted).
_SCENARIOS: dict[str, tuple[str, ...]] = {
    "navigation": ("dinner", "airport", "shopping", "晚飯", "機場", "食飯", "買嘢"),
    "personalization": ("game", "restaurant", "film", "game night", "食飯", "睇戲"),
    # The scenarios that produced #227: a bare hostname and a question about a
    # website. A self-account that answers by naming pages, a version number or
    # a repository is the same failure one layer in — it describes an instance
    # instead of the shape, and the instance is what goes stale in silence.
    "self-description": ("website", "網站", "homepage", "version number",
                         "release notes", "github"),
}


@pytest.fixture(scope="module")
def store() -> SkillStore:
    return SkillStore(SKILLS_DIR)


@pytest.fixture
def skills(store: SkillStore) -> tuple:
    return store.available()


def test_every_shipped_skill_loads_and_is_well_formed(skills):
    assert skills, "at least one skill must ship in nora/skills/"
    for skill in skills:
        assert skill.name.strip()
        assert skill.description.strip()
        assert skill.instructions.strip()


def test_every_description_says_both_when_to_load_and_when_not_to(skills):
    """A skill costs a turn to activate (design/12 §4), so it must earn it — a
    bare live check does NOT need it, a plan does. The description carries both:
    a when-to-load trigger and a negative half naming what it is NOT for."""
    for skill in skills:
        desc = skill.description
        assert len(desc) > 40, f"{skill.name}: description too thin to steer"
        # The negative half — the load-trigger names something it is NOT for.
        assert "Not for" in desc or "not for" in desc, (
            f"{skill.name}: description must say when NOT to load")


def test_every_skill_names_no_scenario(skills):
    """design/17 (operator-insisted): the skill handles the dinner run, the
    airport run and the shopping trip by being about wayfinding, not about any
    one of them. Naming a scenario is the failure mode."""
    for skill in skills:
        haystack = (skill.description + "\n" + skill.instructions).lower()
        for scenario in _SCENARIOS.get(skill.name, ()):
            assert scenario not in haystack, (
                f"{skill.name} names a scenario: {scenario!r}")


def test_every_skill_stays_small(skills):
    """Most of what looks like skill content belongs a layer up or down; what is
    left is genuinely small. A large skill is stealing a lower layer's job."""
    for skill in skills:
        lines = [ln for ln in skill.instructions.splitlines() if ln.strip()]
        assert len(lines) <= 30, (
            f"{skill.name} skill body has {len(lines)} non-blank lines")
