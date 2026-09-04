from __future__ import annotations

from pathlib import Path

from nora.web.skills import build_skills_payload

_SKILL_MD = """\
---
name: {name}
description: {description}
---
Body text — surfaced to the private portal since 2026-09-03 (operator).
"""


async def test_returns_empty_list_when_the_skills_directory_does_not_exist(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert await build_skills_payload(skills_dir=missing) == []


async def test_returns_empty_list_for_an_empty_directory(tmp_path: Path):
    assert await build_skills_payload(skills_dir=tmp_path) == []


async def test_lists_a_real_skill_by_name_and_description(tmp_path: Path):
    skill_dir = tmp_path / "navigation"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        _SKILL_MD.format(name="navigation", description="Load when planning a journey."),
    )
    payload = await build_skills_payload(skills_dir=tmp_path)
    assert payload == [
        {
            "name": "navigation",
            "description": "Load when planning a journey.",
            "instructions": (
                "Body text — surfaced to the private portal since 2026-09-03 (operator)."
            ),
        },
    ]


async def test_surfaces_the_instructions_body_to_the_private_portal(tmp_path: Path):
    """The body IS included since 2026-09-03 (operator decision): the portal
    is private (auth-gated), so it shows what a skill actually says. This does
    NOT touch progressive disclosure — the body still stays out of the model's
    prompt until activation; this is a read-only metadata endpoint."""
    skill_dir = tmp_path / "navigation"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        _SKILL_MD.format(name="navigation", description="desc")
        + "\nSECRET-INSTRUCTIONS-BODY-MARKER\n",
    )
    payload = await build_skills_payload(skills_dir=tmp_path)
    assert "SECRET-INSTRUCTIONS-BODY-MARKER" in str(payload)
    assert set(payload[0]) == {"name", "description", "instructions"}


async def test_skips_a_malformed_skill_rather_than_failing_the_whole_list(tmp_path: Path):
    good = tmp_path / "navigation"
    good.mkdir()
    (good / "SKILL.md").write_text(
        _SKILL_MD.format(name="navigation", description="Real one."),
    )
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("not even frontmatter")

    payload = await build_skills_payload(skills_dir=tmp_path)
    assert payload == [{
        "name": "navigation",
        "description": "Real one.",
        "instructions": "Body text — surfaced to the private portal since 2026-09-03 (operator).",
    }]
