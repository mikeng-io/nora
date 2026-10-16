"""`nora.instructions.frontmatter` — the skeleton both instruction corpora share.

The doctrine's own suites (`tests/test_doctrine_loader.py` and friends) still
cover this behaviour end-to-end through `parse_section`; these tests pin the
extracted piece on its own so a future edit to it fails HERE, naming the
skeleton, instead of only showing up as a doctrine test with a confusing
message.
"""

import re

import pytest

from nora.instructions.frontmatter import (
    InstructionError,
    parse_document,
)

REQUIRED = ("name", "version", "description")


def write(tmp_path, filename, text):
    p = tmp_path / filename
    p.write_text(text, encoding="utf-8")
    return p


def _good(tmp_path, body="The body.\n", name="objective-one"):
    return write(
        tmp_path, "doc.md",
        f"---\nname: {name}\nversion: 3\ndescription: d\nextra: kept\n---\n{body}",
    )


def test_a_well_formed_document_splits_into_name_body_and_meta(tmp_path):
    doc = parse_document(_good(tmp_path), required=REQUIRED)
    assert doc.name == "objective-one"
    assert doc.body == "The body.\n"
    assert doc.path.name == "doc.md"


def test_a_key_the_caller_did_not_require_is_preserved_rather_than_dropped(tmp_path):
    """`meta` is the leftovers, so a caller can decide for itself whether an
    unknown key is metadata or a typo. Dropping it here would make that
    undecidable one layer up."""
    doc = parse_document(_good(tmp_path), required=REQUIRED)
    assert doc.meta["extra"] == "kept"
    assert doc.meta["version"] == 3
    assert "name" not in doc.meta          # returned as an attribute instead


def test_the_digest_is_over_the_body_only(tmp_path):
    """The frontmatter carries the version, the description and the change
    policy — none of which reaches a model. Hashing it would make an editorial
    `description` change look like a change to what the model was told, which is
    the exact confusion `instruction_sha256` exists to remove."""
    import hashlib

    a = parse_document(_good(tmp_path, body="Same body.\n"), required=REQUIRED)
    write(
        tmp_path, "doc.md",
        "---\nname: objective-one\nversion: 99\ndescription: REWRITTEN\n---\nSame body.\n",
    )
    b = parse_document(tmp_path / "doc.md", required=REQUIRED)
    assert a.sha256 == b.sha256
    assert a.sha256 == hashlib.sha256(b"Same body.\n").hexdigest()


def test_editing_the_body_changes_the_digest(tmp_path):
    a = parse_document(_good(tmp_path, body="One.\n"), required=REQUIRED).sha256
    b = parse_document(_good(tmp_path, body="Two.\n"), required=REQUIRED).sha256
    assert a != b


def test_a_file_with_no_frontmatter_is_refused(tmp_path):
    path = write(tmp_path, "doc.md", "Just prose, no frontmatter at all.\n")
    with pytest.raises(InstructionError, match=r"doc\.md: missing YAML frontmatter"):
        parse_document(path, required=REQUIRED)


def test_unparseable_yaml_is_refused_and_says_so(tmp_path):
    path = write(tmp_path, "doc.md", "---\nname: [unclosed\n---\nBody.\n")
    with pytest.raises(InstructionError, match=r"doc\.md: unparseable frontmatter"):
        parse_document(path, required=REQUIRED)


def test_frontmatter_that_is_not_a_mapping_is_refused(tmp_path):
    path = write(tmp_path, "doc.md", "---\n- a\n- b\n---\nBody.\n")
    with pytest.raises(InstructionError, match=r"doc\.md: frontmatter must be a mapping"):
        parse_document(path, required=REQUIRED)


def test_a_missing_required_key_names_which_key(tmp_path):
    """Naming the key is the whole value of the check. "the file is invalid"
    leaves the operator diffing frontmatter by eye against another file."""
    path = write(tmp_path, "doc.md", "---\nname: a\nversion: 1\n---\nBody.\n")
    with pytest.raises(
        InstructionError,
        match=r"missing required frontmatter key\(s\): 'description'",
    ):
        parse_document(path, required=REQUIRED)


def test_name_is_required_even_when_the_caller_forgot_to_list_it(tmp_path):
    """`name` comes back as an attribute, so a caller omitting it from
    `required` would otherwise get a bare KeyError instead of the
    filename-first message every other malformed shape gets."""
    path = write(tmp_path, "doc.md", "---\nversion: 1\n---\nBody.\n")
    with pytest.raises(InstructionError, match=r"missing required frontmatter key\(s\): 'name'"):
        parse_document(path, required=("version",))


def test_a_name_outside_the_charset_is_refused_with_the_pattern(tmp_path):
    path = _good(tmp_path, name="Not_A_Valid_Name")
    with pytest.raises(InstructionError, match=r"must match \^\[a-z0-9-\]\{1,64\}\$"):
        parse_document(path, required=REQUIRED)


def test_the_charset_is_the_callers_to_choose(tmp_path):
    path = _good(tmp_path, name="lower")
    with pytest.raises(InstructionError, match="must match"):
        parse_document(path, required=REQUIRED, name_re=re.compile(r"^[A-Z]+$"))


def test_the_caller_chooses_the_exception_class(tmp_path):
    """`nora.doctrine.loader._scan` catches `DoctrineError` to decide
    skip-vs-abort in lenient mode; re-wrapping here would change which
    errors that `except` sees. Passing the class down is what keeps the
    extraction behaviour-preserving."""
    class Mine(Exception):
        pass

    path = write(tmp_path, "doc.md", "no frontmatter")
    with pytest.raises(Mine):
        parse_document(path, required=REQUIRED, error=Mine)
