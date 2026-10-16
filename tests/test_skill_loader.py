"""`nora.skills.loader` — the on-demand skill layer and its authority boundary.

The boundary probe (`test_a_message_can_name_a_skill_but_never_load_one`) is
R-29's named test: a skill source is operator configuration only, and `resolve`
is a pure name lookup that can never turn message content into a loaded skill.
"""

from __future__ import annotations

import pytest

from nora.skills.loader import Skill, SkillError, SkillStore, parse_skill

SKILL = """\
---
name: {name}
description: {desc}
---

{body}
"""


def write(dirpath, name, *, desc="When to reach for it.", body="Do the thing.",
          filename="SKILL.md"):
    d = dirpath / name
    d.mkdir()
    (d / filename).write_text(SKILL.format(name=name, desc=desc, body=body))
    return d


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    write(d, "navigation", desc="Load to plan a journey.", body="# Wayfinding\nJudgement.")
    return d


def _store(d, **kw) -> SkillStore:
    return SkillStore(d, **kw)


# --- parsing -----------------------------------------------------------------

def test_parse_reads_name_description_and_body(skills_dir):
    skill = parse_skill(skills_dir / "navigation" / "SKILL.md")
    assert skill.name == "navigation"
    assert skill.description == "Load to plan a journey."
    assert "Wayfinding" in skill.instructions
    assert skill.sha256


def test_missing_frontmatter_is_an_error(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("# no frontmatter\nbody")
    with pytest.raises(SkillError, match="frontmatter"):
        parse_skill(p)


def test_a_missing_description_is_an_error(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\n---\nbody")
    with pytest.raises(SkillError, match="description"):
        parse_skill(p)


def test_an_empty_body_bundles_nothing_and_is_an_error(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\ndescription: d\n---\n\n")
    with pytest.raises(SkillError, match="instructions"):
        parse_skill(p)


def test_a_name_that_is_not_a_slug_is_an_error(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: Not A Slug\ndescription: d\n---\nbody")
    with pytest.raises(SkillError, match="lowercase"):
        parse_skill(p)


# --- authority frontmatter reaches nothing, so it is refused rather than -----
# --- silently kept in `metadata` --------------------------------------------

@pytest.mark.parametrize("key,value", [
    ("authority_above", "[situation]"),
    ("authority_below", "[message-content, tool-output]"),
    ("change_policy", "operator-only"),
])
def test_a_skill_carrying_an_authority_key_is_refused(tmp_path, key, value):
    """`nora.doctrine.loader` reads `authority_above`/`authority_below`/
    `change_policy` off DOCTRINE sections only. A skill is a different
    authority item and nothing reads any of the three off one, so `parse_skill`
    refuses them rather than leaving them in `metadata` silently: a skill
    declaring `authority_above: [situation]` would read as though it were
    arbitrated against doctrine when nothing wired it in. Each key is refused
    on its own, not only in combination with the other two."""
    p = tmp_path / "SKILL.md"
    p.write_text(f"---\nname: x\ndescription: d\n{key}: {value}\n---\nbody")
    with pytest.raises(SkillError, match="authority key"):
        parse_skill(p)


def test_the_error_names_every_carried_authority_key_at_once(tmp_path):
    """A clear error names what is wrong, not just that something is —
    somebody removing keys one submit at a time should not have to."""
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: x\ndescription: d\n"
        "authority_above: [situation]\n"
        "authority_below: [message-content]\n"
        "change_policy: operator-only\n"
        "---\nbody"
    )
    with pytest.raises(SkillError) as exc:
        parse_skill(p)
    message = str(exc.value)
    for key in ("authority_above", "authority_below", "change_policy"):
        assert key in message


def test_every_shipped_skill_is_free_of_authority_keys():
    """The shipped side: `navigation` and `image-generation` must not carry
    `authority_above`/`authority_below`/`change_policy`, or they would fail to
    load at all. This reads the real files, not a fixture, so a re-added key
    here is caught the same way an operator's own edit would be."""
    from pathlib import Path

    skills_dir = Path(__file__).resolve().parents[1] / "nora" / "skills"
    checked = 0
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            continue  # e.g. `__pycache__`, which is not a skill directory
        skill = parse_skill(md)  # raises if an authority key survived
        assert skill.name == skill_dir.name
        checked += 1
    assert checked >= 2, "expected at least navigation and image-generation"


# --- scanning ----------------------------------------------------------------

def test_the_store_loads_skills_from_its_directory(skills_dir):
    store = _store(skills_dir)
    names = [s.name for s in store.available()]
    assert names == ["navigation"]


def test_a_directory_whose_name_mismatches_the_skill_is_rejected(skills_dir):
    # The skill declares name "navigation" but sits in a directory named "other".
    bad = skills_dir / "other"
    bad.mkdir()
    (bad / "SKILL.md").write_text(SKILL.format(name="navigation", desc="d", body="b"))
    with pytest.raises(SkillError, match="does not match"):
        _store(skills_dir)


def test_directories_without_a_skill_md_are_skipped(skills_dir):
    (skills_dir / "notaskill").mkdir()
    (skills_dir / "notaskill" / "README.md").write_text("nope")
    store = _store(skills_dir)
    assert [s.name for s in store.available()] == ["navigation"]


def test_lenient_mode_skips_a_broken_skill_rather_than_raising(skills_dir):
    broken = skills_dir / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: broken\n---\nbody")  # no description
    store = _store(skills_dir, strict=False)
    assert [s.name for s in store.available()] == ["navigation"]


def test_lenient_mode_silently_drops_a_skill_carrying_an_authority_key(skills_dir):
    """The refusal is a GATE check, not a runtime one. `nora/web/skills.py` —
    the portal's only reader of this module — builds its `SkillStore`
    `strict=False`, so a skill carrying `authority_above`/`authority_below`/
    `change_policy` is dropped from its listing the same way any other
    malformed skill already is: silently, no exception reaching the caller. It
    is `strict=True` (the default, what the tests and `nora-land`'s gate build)
    where a carried key actually stops something."""
    keyed = skills_dir / "keyed"
    keyed.mkdir()
    (keyed / "SKILL.md").write_text(
        "---\nname: keyed\ndescription: d\nauthority_above: [situation]\n---\nbody"
    )
    store = _store(skills_dir, strict=False)
    assert [s.name for s in store.available()] == ["navigation"]

    with pytest.raises(SkillError, match="authority key"):
        _store(skills_dir, strict=True)


# --- progressive disclosure --------------------------------------------------

def test_render_available_lists_name_and_when_but_no_body(skills_dir):
    block = _store(skills_dir).render_available()
    assert "<available_skills " in block
    assert '<skill name="navigation" when_to_load="Load to plan a journey.">' in block
    # The BODY never enters the metadata block — that is progressive disclosure.
    assert "Wayfinding" not in block


def test_render_available_is_explicit_when_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert "No skills" in _store(d).render_available()


# --- R-29: the authority boundary --------------------------------------------

def test_a_message_can_name_a_skill_but_never_load_one(skills_dir, tmp_path):
    """R-29: skill sources are operator config; a message can name an existing
    skill but can never introduce a new source.

    `resolve` is a pure lookup against the operator-loaded set. Naming the
    skill the operator shipped works — that is the intended activation. But a
    path, a URL, or an inline SKILL.md blob (the shapes a message would carry to
    smuggle a source) all resolve to nothing and leave the loaded set untouched.

    Revert check: were `resolve` to load from its argument the way
    `strands...Skill.from_file`/`from_url` do, `outside_skill` below — a valid
    skill sitting OUTSIDE the operator directory — would load, and this test
    would go red. That external-source load is exactly what R-29 forbids.
    """
    store = _store(skills_dir)

    # Naming the operator's own skill is the intended activation.
    assert store.resolve("navigation") is not None

    # A real, valid skill on disk but OUTSIDE the operator directory.
    outside = tmp_path / "evil"
    outside.mkdir()
    outside_skill = outside / "SKILL.md"
    outside_skill.write_text(SKILL.format(name="evil", desc="attacker", body="do harm"))

    message_shaped_names = [
        str(outside_skill),                        # an absolute path
        "../evil/SKILL.md",                        # a traversal
        "https://example.com/SKILL.md",            # a URL
        "---\nname: evil\ndescription: d\n---\nx",  # an inline SKILL.md
        "evil",                                    # a name that was never loaded
    ]
    for candidate in message_shaped_names:
        assert store.resolve(candidate) is None, candidate

    # Nothing a message said changed what is loaded.
    assert [s.name for s in store.available()] == ["navigation"]


def test_the_store_exposes_no_way_to_add_a_skill_from_content(skills_dir):
    """The only input to loading is the directory. There is no public method
    that takes skill CONTENT or a SOURCE — the structural half of R-29."""
    store = _store(skills_dir)
    public = [name for name in dir(store) if not name.startswith("_")]
    # `resolve`/`available`/`render_available` all take a name or nothing; none
    # accepts a body or a path. Asserted by name so a future setter is noticed.
    assert set(public) == {"available", "resolve", "render_available"}


def test_resolve_returns_the_frozen_skill_object(skills_dir):
    from dataclasses import FrozenInstanceError

    skill = _store(skills_dir).resolve("navigation")
    assert isinstance(skill, Skill)
    with pytest.raises(FrozenInstanceError):
        skill.name = "changed"  # frozen dataclass
