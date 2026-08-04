"""Structural boundary tests (spec C.1). These are architecture, enforced."""

import ast
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


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names this file imports, via AST — not substring matching.

    Ignores relative imports (level > 0): those are intra-package and can never
    reach a third-party dependency.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


# Allowlisted by PACKAGE, not by file path, so moving a module within agent/ or
# splitting agent/tools.py into a package does not require editing this test.
STRANDS_ALLOWED_PACKAGES = {"agent"}


def test_strands_import_boundary():
    """Only nora/agent/** may import strands."""
    offenders = []
    for path in _python_files():
        if "strands" not in _imported_modules(path):
            continue
        package = path.relative_to(PKG).parts[0]
        if package not in STRANDS_ALLOWED_PACKAGES:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"strands imported outside the agent boundary: {offenders}"
