
import pytest

from nora.doctrine.loader import DoctrineError, DoctrineStore, parse_section
from nora.obs.metrics import Metrics

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
def doctrine_dir(tmp_path):
    d = tmp_path / "doctrine"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, body="Who you are.")
    write(d, "voice.md", name="voice", order=20, body="The sound.")
    write(d, "conduct.md", name="conduct", order=30, body="How you behave.")
    write(d, "constraints.md", name="guardrails", order=40, body="Hard rules.")
    return d


def _store(d, **kw) -> DoctrineStore:
    return DoctrineStore(d, canary="CANARY-XYZ", display_name="Nora",
                        trigger="nora.000", language_hint="hk", metrics=Metrics(), **kw)


def test_parse_section_reads_frontmatter_and_body(doctrine_dir):
    s = parse_section(doctrine_dir / "identity.md")
    assert (s.name, s.order, s.version, s.applies_to) == ("soul", 10, 1, ("tag",))
    assert s.body.strip() == "Who you are."
    assert len(s.sha256) == 64


def test_sections_are_ordered_by_order_not_filename(doctrine_dir):
    write(doctrine_dir, "aaa.md", name="last", order=99, body="Z")
    doctrine = _store(doctrine_dir).get(applies_to="tag")
    assert [s.name for s in doctrine.sections] == \
        ["soul", "voice", "conduct", "guardrails", "last"]


# test_ties_on_order_break_on_filename (from the plan) is omitted: its premise
# (files with duplicate `order` values are allowed and tie-broken by filename) is
# incompatible with _scan()'s strict, unconditional duplicate-order rejection,
# which is verbatim-specified in the same plan task and has its own passing test
# (test_duplicate_order_is_a_startup_failure). Order ties cannot occur in any
# directory that passes validation, so filename tie-breaking is unreachable
# defensive code. Flagged as an open plan contradiction — see ledger.


def test_applies_to_filters_the_assembly(doctrine_dir):
    write(doctrine_dir, "future.md", name="classifier-only", order=50, applies="classifier")
    names = [s.name for s in _store(doctrine_dir).get(applies_to="tag").sections]
    assert "classifier-only" not in names


def test_applies_to_always_participates_in_every_assembly(doctrine_dir):
    write(doctrine_dir, "common.md", name="common", order=5, applies="always")
    for assembly in ("tag", "classifier"):
        names = [s.name for s in _store(doctrine_dir).get(applies_to=assembly).sections]
        assert "common" in names


def test_missing_frontmatter_is_a_startup_failure_under_strict(doctrine_dir):
    (doctrine_dir / "broken.md").write_text("no frontmatter here")
    with pytest.raises(DoctrineError, match="frontmatter"):
        _store(doctrine_dir, strict=True).get(applies_to="tag")


def test_missing_frontmatter_is_skipped_with_a_warning_under_lenient(doctrine_dir, caplog):
    (doctrine_dir / "broken.md").write_text("no frontmatter here")
    doctrine = _store(doctrine_dir, strict=False).get(applies_to="tag")
    assert len(doctrine.sections) == 4
    assert any("broken.md" in r.message for r in caplog.records)


def test_duplicate_name_is_a_startup_failure(doctrine_dir):
    write(doctrine_dir, "dup.md", name="soul", order=11)
    with pytest.raises(DoctrineError, match="duplicate name"):
        _store(doctrine_dir).get(applies_to="tag")


def test_duplicate_order_is_a_startup_failure(doctrine_dir):
    write(doctrine_dir, "dup.md", name="other", order=10)
    with pytest.raises(DoctrineError, match="duplicate order"):
        _store(doctrine_dir).get(applies_to="tag")


def test_invalid_name_charset_is_a_startup_failure(doctrine_dir):
    write(doctrine_dir, "bad.md", name="Soul Section", order=60)
    with pytest.raises(DoctrineError, match="name"):
        _store(doctrine_dir).get(applies_to="tag")


def test_missing_required_field_is_a_startup_failure(doctrine_dir):
    (doctrine_dir / "nofield.md").write_text("---\nname: x\ndescription: d\norder: 70\n---\nbody")
    with pytest.raises(DoctrineError, match="version"):
        _store(doctrine_dir).get(applies_to="tag")


def test_unknown_frontmatter_keys_are_preserved_in_metadata(doctrine_dir):
    p = doctrine_dir / "extra.md"
    p.write_text(
        "---\nname: extra\ndescription: d\norder: 80\napplies_to: [tag]\nversion: 2\n"
        "author: miles\n---\nbody"
    )
    section = parse_section(p)
    assert section.metadata == {"author": "miles"}


def test_an_empty_doctrine_directory_is_a_startup_failure(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DoctrineError, match="no doctrine files"):
        DoctrineStore(empty, canary="c", metrics=Metrics()).get(applies_to="tag")


def test_non_markdown_files_are_ignored(doctrine_dir):
    (doctrine_dir / "loader.py").write_text("# code, not doctrine")
    (doctrine_dir / "notes.txt").write_text("scratch")
    assert len(_store(doctrine_dir).get(applies_to="tag").sections) == 4
