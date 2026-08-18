from __future__ import annotations

from pathlib import Path

from nora.web.skills import build_skills_payload

_SKILL_MD = """\
---
name: {name}
description: {description}
---
Body text — never surfaced to the web portal, only name/description are.
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
        {"name": "navigation", "description": "Load when planning a journey."},
    ]


async def test_never_surfaces_the_instructions_body(tmp_path: Path):
    """The body is the only thing that reaches the model, and only on
    activation (progressive disclosure) — it must never reach a browser
    that merely lists what's available."""
    skill_dir = tmp_path / "navigation"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        _SKILL_MD.format(name="navigation", description="desc")
        + "\nSECRET-INSTRUCTIONS-BODY-MARKER\n",
    )
    payload = await build_skills_payload(skills_dir=tmp_path)
    assert "SECRET-INSTRUCTIONS-BODY-MARKER" not in str(payload)
    assert set(payload[0]) == {"name", "description"}


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
    assert payload == [{"name": "navigation", "description": "Real one."}]
