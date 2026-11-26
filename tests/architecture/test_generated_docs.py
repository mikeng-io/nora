"""The generated documentation views equal their regeneration from frontmatter."""

from __future__ import annotations

import shutil
from pathlib import Path

from scripts.generate_docs_index import render, stale

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "documents" / "tree"


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in root.rglob("*.md")}


def test_the_views_are_derived_from_frontmatter():
    views = render(_tree(FIXTURE))
    assert "| design | [design/platform/README.md](design/platform/README.md) |" in views["docs/README.md"]
    assert "| [platform](platform/README.md) | how a messaging platform connects to Nora | platform | NOR-1 |" in views["docs/design/README.md"]
    assert (
        "| bridge | the process that carries one platform's messages to and from Nora "
        "| connector | [design/platform/README.md](design/platform/README.md) |"
    ) in views["docs/glossary.md"]


def test_a_written_view_is_current_and_a_hand_edit_is_stale(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    for path, text in render(_tree(root)).items():
        (root / path).write_text(text, encoding="utf-8")
    assert stale(root, _tree(root)) == []
    glossary = root / "docs/glossary.md"
    glossary.write_text(glossary.read_text(encoding="utf-8") + "| extra | hand-written | | |\n")
    assert stale(root, _tree(root)) == ["docs/glossary.md"]


def test_a_changed_source_makes_its_view_stale(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    for path, text in render(_tree(root)).items():
        (root / path).write_text(text, encoding="utf-8")
    source = root / "docs/engineering/process.md"
    source.write_text(source.read_text(encoding="utf-8").replace("how a change is checked", "how review works"))
    assert "docs/README.md" in stale(root, _tree(root))
