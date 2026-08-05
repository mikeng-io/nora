
import pytest

from nora.obs.metrics import Metrics
from nora.persona.loader import PersonaError, PersonaStore, parse_section

FM = """\
---
name: {name}
description: {desc}
order: {order}
applies_to: [{applies}]
version: {version}
---

{body}
"""


def write(dirpath, filename, *, name, order, applies="tag", version=1,
          desc="d", body="Body text."):
    p = dirpath / filename
    p.write_text(FM.format(name=name, desc=desc, order=order, applies=applies,
                           version=version, body=body))
    return p


@pytest.fixture
def persona_dir(tmp_path):
    d = tmp_path / "persona"
    d.mkdir()
    write(d, "soul.md", name="soul", order=10, body="Who you are.")
    write(d, "voice.md", name="voice", order=20, body="The sound.")
    write(d, "conduct.md", name="conduct", order=30, body="How you behave.")
    write(d, "guardrails.md", name="guardrails", order=40, body="Hard rules.")
    return d


def _store(d, **kw) -> PersonaStore:
    return PersonaStore(d, canary="CANARY-XYZ", display_name="Nora",
                        trigger="nora.000", language_hint="hk", metrics=Metrics(), **kw)


def test_parse_section_reads_frontmatter_and_body(persona_dir):
    s = parse_section(persona_dir / "soul.md")
    assert (s.name, s.order, s.version, s.applies_to) == ("soul", 10, 1, ("tag",))
    assert s.body.strip() == "Who you are."
    assert len(s.sha256) == 64


def test_sections_are_ordered_by_order_not_filename(persona_dir):
    write(persona_dir, "aaa.md", name="last", order=99, body="Z")
    persona = _store(persona_dir).get(applies_to="tag")
    assert [s.name for s in persona.sections] == \
        ["soul", "voice", "conduct", "guardrails", "last"]


# test_ties_on_order_break_on_filename (from the plan) is omitted: its premise
# (files with duplicate `order` values are allowed and tie-broken by filename) is
# incompatible with _scan()'s strict, unconditional duplicate-order rejection,
# which is verbatim-specified in the same plan task and has its own passing test
# (test_duplicate_order_is_a_startup_failure). Order ties cannot occur in any
# directory that passes validation, so filename tie-breaking is unreachable
# defensive code. Flagged as an open plan contradiction — see ledger.


def test_applies_to_filters_the_assembly(persona_dir):
    write(persona_dir, "future.md", name="classifier-only", order=50, applies="classifier")
    names = [s.name for s in _store(persona_dir).get(applies_to="tag").sections]
    assert "classifier-only" not in names


def test_applies_to_always_participates_in_every_assembly(persona_dir):
    write(persona_dir, "common.md", name="common", order=5, applies="always")
    for assembly in ("tag", "classifier"):
        names = [s.name for s in _store(persona_dir).get(applies_to=assembly).sections]
        assert "common" in names


def test_missing_frontmatter_is_a_startup_failure_under_strict(persona_dir):
    (persona_dir / "broken.md").write_text("no frontmatter here")
    with pytest.raises(PersonaError, match="frontmatter"):
        _store(persona_dir, strict=True).get(applies_to="tag")


def test_missing_frontmatter_is_skipped_with_a_warning_under_lenient(persona_dir, caplog):
    (persona_dir / "broken.md").write_text("no frontmatter here")
    persona = _store(persona_dir, strict=False).get(applies_to="tag")
    assert len(persona.sections) == 4
    assert any("broken.md" in r.message for r in caplog.records)


def test_duplicate_name_is_a_startup_failure(persona_dir):
    write(persona_dir, "dup.md", name="soul", order=11)
    with pytest.raises(PersonaError, match="duplicate name"):
        _store(persona_dir).get(applies_to="tag")


def test_duplicate_order_is_a_startup_failure(persona_dir):
    write(persona_dir, "dup.md", name="other", order=10)
    with pytest.raises(PersonaError, match="duplicate order"):
        _store(persona_dir).get(applies_to="tag")


def test_invalid_name_charset_is_a_startup_failure(persona_dir):
    write(persona_dir, "bad.md", name="Soul Section", order=60)
    with pytest.raises(PersonaError, match="name"):
        _store(persona_dir).get(applies_to="tag")


def test_missing_required_field_is_a_startup_failure(persona_dir):
    (persona_dir / "nofield.md").write_text("---\nname: x\ndescription: d\norder: 70\n---\nbody")
    with pytest.raises(PersonaError, match="version"):
        _store(persona_dir).get(applies_to="tag")


def test_unknown_frontmatter_keys_are_preserved_in_metadata(persona_dir):
    p = persona_dir / "extra.md"
    p.write_text(
        "---\nname: extra\ndescription: d\norder: 80\napplies_to: [tag]\nversion: 2\n"
        "author: miles\n---\nbody"
    )
    section = parse_section(p)
    assert section.metadata == {"author": "miles"}


def test_an_empty_persona_directory_is_a_startup_failure(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PersonaError, match="no persona files"):
        PersonaStore(empty, canary="c", metrics=Metrics()).get(applies_to="tag")


def test_non_markdown_files_are_ignored(persona_dir):
    (persona_dir / "loader.py").write_text("# code, not persona")
    (persona_dir / "notes.txt").write_text("scratch")
    assert len(_store(persona_dir).get(applies_to="tag").sections) == 4
