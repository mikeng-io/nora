"""A changed document follows docs/engineering/documents.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_documents import (
    MAX_LINES,
    STAGED_TARGETS,
    check,
    tree_rules,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "tree"


def _front(body: str) -> str:
    return f"---\n{body}\n---\n\n# Title\n"


GOOD_DESIGN = _front("class: design\nsubject: how a thing works\nowns: [thing]\narea: thing")
GOOD_ENGINEERING = _front("class: engineering\nsubject: how a change is checked\nowns: []")


@pytest.mark.parametrize(
    "path, text",
    [
        ("docs/design/thing/README.md", GOOD_DESIGN),
        ("docs/engineering/process.md", GOOD_ENGINEERING),
        ("docs/adr/0042-a-decision.md", _front("class: adr\nsubject: why x\nowns: []")),
        (
            "docs/guide/x.md",
            _front("class: guide\nsubject: x for a reader\nowns: []\nplain: {x: y}"),
        ),
        ("README.md", "# Nora\n"),
        ("docs/README.md", "generated\n"),
    ],
)
def test_a_document_that_follows_the_rules_passes(path, text):
    assert check(path, text) == []


@pytest.mark.parametrize(
    "path, text, rule",
    [
        ("docs/engineering/x.md", "# No frontmatter\n", "frontmatter-missing"),
        ("docs/engineering/x.md", _front("class: memo\nsubject: s\nowns: []"), "frontmatter-class"),
        ("docs/design/x/README.md", GOOD_ENGINEERING, "class-home"),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: ''\nowns: []"),
            "frontmatter-subject",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: x"),
            "frontmatter-owns",
        ),
        (
            "docs/design/x/README.md",
            _front("class: design\nsubject: s\nowns: []"),
            "frontmatter-area",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: []\narea: a"),
            "frontmatter-area",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: []\nlinks: [{to: a, rel: likes}]"),
            "frontmatter-links",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: []\nstatus: building"),
            "frontmatter-unknown-field",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: []\nplain: {a: b}"),
            "frontmatter-plain",
        ),
        ("docs/engineering/x.md", GOOD_ENGINEERING + "Codex reviews it.\n", "vendor-name"),
        ("docs/engineering/2026-11-01-review.md", GOOD_ENGINEERING, "numbered-or-dated-name"),
        ("docs/design/03-shape.md", GOOD_DESIGN, "numbered-or-dated-name"),
        ("docs/design/loose.md", GOOD_DESIGN, "design-root"),
        ("docs/design/x/README.md", GOOD_DESIGN + "x\n" * MAX_LINES, "over-budget"),
        ("ARCHITECTURE.md", "# Architecture\n", "root-document"),
        (
            "docs/engineering/x.md",
            _front("class: [engineering]\nsubject: s\nowns: []"),
            "frontmatter-class",
        ),
        (
            "docs/design/x/README.md",
            _front("class: design\nsubject: s\nowns: []\narea: []"),
            "frontmatter-area",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: []\nlinks: [{to: 7, rel: uses}]"),
            "frontmatter-links",
        ),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: s\nowns: [{a: ''}]"),
            "frontmatter-owns",
        ),
        ("docs/engineering/x.md", GOOD_ENGINEERING + "the codex reviews it\n", "vendor-name"),
        (
            "docs/engineering/x.md",
            _front("class: engineering\nsubject: what Codex checks\nowns: []"),
            "vendor-name",
        ),
        ("docs/design/diagram.png", "", "design-root"),
    ],
)
def test_each_rule_fails_by_name(path, text, rule):
    assert rule in check(path, text)


def test_a_file_name_and_a_common_word_are_not_vendor_names():
    assert (
        check("docs/engineering/x.md", GOOD_ENGINEERING + "See `CLAUDE.md`. A database cursor.\n")
        == []
    )


def test_a_link_must_name_a_repository_file():
    from scripts.check_documents import resolves

    assert resolves("docs/engineering/documents.md")
    assert not resolves("/") and not resolves("docs") and not resolves("../outside.md")


def test_the_decision_index_is_not_held_to_the_bundle_budget():
    long = _front("class: decision\nsubject: what holds\nowns: []") + "x\n" * (MAX_LINES + 50)
    assert "over-budget" not in check("docs/design/decisions.md", long)


def test_a_bundle_readme_may_use_the_full_300_line_budget():
    assert check("docs/design/x/README.md", GOOD_DESIGN + "x\n" * (MAX_LINES - 10)) == []


def _tree() -> dict[str, str]:
    return {
        str(p.relative_to(FIXTURE)): p.read_text(encoding="utf-8") for p in FIXTURE.rglob("*.md")
    }


def _exists(tree: dict[str, str]):
    return lambda target: target in tree


def test_a_consistent_tree_has_no_cross_file_violation():
    tree = _tree()
    assert tree_rules(tree, _exists(tree)) == {}
    assert all(check(path, text) == [] for path, text in tree.items())


def test_a_subject_or_term_claimed_twice_fails_in_both_files():
    tree = _tree()
    tree["docs/engineering/copy.md"] = tree["docs/engineering/process.md"]
    found = tree_rules(tree, _exists(tree))
    for path in ("docs/engineering/copy.md", "docs/engineering/process.md"):
        assert {"subject-duplicate", "owns-duplicate"} <= set(found[path])


def test_an_unresolved_link_fails_unless_staged():
    tree = _tree()
    tree["docs/engineering/process.md"] = _front(
        "class: engineering\nsubject: how a change is checked\nowns: [review]\n"
        "links: [{to: docs/missing.md, rel: uses}, {to: docs/glossary.md, rel: uses}]"
    )
    assert tree_rules(tree, _exists(tree)) == {"docs/engineering/process.md": ["link-unresolved"]}
    assert "docs/glossary.md" in STAGED_TARGETS


def test_every_staged_target_names_the_unit_that_removes_it():
    assert all(unit in {"U2", "U3", "U4", "U5"} for unit in STAGED_TARGETS.values())


def test_the_hook_judges_only_the_documents_a_commit_changes(tmp_path, monkeypatch):
    import subprocess

    import scripts.check_documents as gate

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)  # noqa: S603

    git("init", "-q")
    (tmp_path / "docs/engineering").mkdir(parents=True)
    (tmp_path / "docs/engineering/legacy.md").write_text("# untouched, no frontmatter\n")
    git("add", "-A")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / "docs/engineering/new.md").write_text(GOOD_ENGINEERING)
    git("add", "docs/engineering/new.md")
    assert gate.main(["--staged"]) == 0
    (tmp_path / "docs/engineering/bad.md").write_text("# no frontmatter\n")
    git("add", "docs/engineering/bad.md")
    assert gate.main(["--staged"]) == 1
    assert gate.changed(["--staged"]) == ["docs/engineering/bad.md", "docs/engineering/new.md"]
    (tmp_path / "NOTES.md").write_text("# a new root document\n")
    git("add", "NOTES.md")
    assert "NOTES.md" in gate.changed(["--staged"])
