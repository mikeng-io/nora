"""ARCHITECTURE.md is a map, and maps rot. This pins it to reality."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_doc_covers_every_package():
    doc = (ROOT / "ARCHITECTURE.md").read_text()
    packages = sorted(p.name for p in (ROOT / "nora").iterdir()
                      if p.is_dir() and not p.name.startswith(("_", ".")))
    missing = [p for p in packages if f"`{p}/`" not in doc and f"nora/{p}" not in doc]
    assert not missing, f"ARCHITECTURE.md does not mention: {missing}"


def test_claude_md_carries_the_operational_constraints():
    text = (ROOT / "CLAUDE.md").read_text().lower()
    for phrase in ("--network=host", "signal-cli", "uv add", "get /health"):
        assert phrase in text, f"CLAUDE.md must carry: {phrase}"


def test_decision_index_lists_the_amended_decisions():
    text = (ROOT / "design" / "09-decision-index.md").read_text()
    for ident in ("D1", "D4", "D6", "DEC-3", "DEC-9", "R-5", "R-10", "R-12"):
        assert ident in text, f"decision index missing {ident}"
