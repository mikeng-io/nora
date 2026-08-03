"""Structural boundary tests (spec C.1). These are architecture, enforced."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "nora"


def _python_files():
    return [p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts]


def test_persona_read_boundary():
    """Only persona/loader.py may read persona files."""
    offenders = []
    for path in _python_files():
        if path == PKG / "persona" / "loader.py":
            continue
        text = path.read_text()
        if ".md" in text and ("read_text" in text or "open(" in text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"persona files read outside loader.py: {offenders}"


def test_strands_import_boundary():
    """Only agent/factory.py and agent/guards.py may import strands."""
    allowed = {PKG / "agent" / "factory.py", PKG / "agent" / "guards.py",
               PKG / "agent" / "tools.py"}
    offenders = []
    for path in _python_files():
        if path in allowed:
            continue
        text = path.read_text()
        if "import strands" in text or "from strands" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"strands imported outside the agent boundary: {offenders}"
