"""ARCHITECTURE.md is a map, and maps rot. This pins it to reality."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_doc_covers_every_package():
    """Covers both sub-packages (dirs) and top-level modules (`handler.py`,
    `tag.py`, `persons.py`, ...) under nora/ — a new top-level module follows the
    same "must be in the map" rule a new package does, and is not exempt just
    because it is not a directory."""
    doc = (ROOT / "ARCHITECTURE.md").read_text()
    packages = sorted(p.name for p in (ROOT / "nora").iterdir()
                      if p.is_dir() and not p.name.startswith(("_", ".")))
    modules = sorted(p.name for p in (ROOT / "nora").glob("*.py")
                     if not p.name.startswith("_"))
    missing_packages = [p for p in packages if f"`{p}/`" not in doc and f"nora/{p}" not in doc]
    missing_modules = [m for m in modules if f"`{m}`" not in doc and f"nora/{m}" not in doc]
    missing = missing_packages + missing_modules
    assert not missing, f"ARCHITECTURE.md does not mention: {missing}"


def test_agents_md_carries_the_operational_constraints():
    text = (ROOT / "AGENTS.md").read_text().lower()
    for phrase in ("--network=host", "signal-cli", "uv add", "get /health"):
        assert phrase in text, f"AGENTS.md must carry: {phrase}"


def test_claude_md_is_a_pointer_and_not_a_second_copy():
    """Two copies of the rules drift, and the one that drifts is the one nobody
    read. `CLAUDE.md` exists only so Claude Code finds `AGENTS.md` (ADR 0010)."""
    text = (ROOT / "CLAUDE.md").read_text()
    assert "AGENTS.md" in text, "CLAUDE.md must point at AGENTS.md"
    assert len(text.splitlines()) < 30, (
        "CLAUDE.md has grown past a pointer — the rules belong in AGENTS.md"
    )


def test_decision_index_lists_the_amended_decisions():
    text = (ROOT / "design" / "09-decision-index.md").read_text()
    for ident in ("D1", "D4", "D6", "DEC-3", "DEC-9", "R-5", "R-10", "R-12"):
        assert ident in text, f"decision index missing {ident}"
