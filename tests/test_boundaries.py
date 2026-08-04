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


def _imported_dotted_modules(path: Path) -> set[str]:
    """Like `_imported_modules`, but keeps the FULL dotted path for absolute
    imports instead of truncating to the top-level package name.

    That distinction matters for the allow-list below: truncating
    `nora.store` to `nora` would make it indistinguishable from a legitimate
    sibling import of `nora.domain.models`, and silently let `from nora.store
    import db` through. Still ignores relative imports (level > 0): those are
    intra-package and can never reach outside `nora.domain`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_domain_imports_only_the_standard_library():
    """domain/ may import the stdlib and its own siblings — nothing else.
    A deny-list only catches names someone thought of; this catches everything.

    Supersedes the old deny-list (`DOMAIN_BANNED`): a deny-list of eight
    hardcoded framework names passes silently on anything not on the list —
    `import aiosqlite` or `from nora.store import db` both slipped through it
    with every gate green. An allow-list of "stdlib + own siblings" cannot be
    bypassed by picking an unlisted name.
    """
    import sys

    allowed = set(sys.stdlib_module_names)
    offenders = []
    for path in (PKG / "domain").rglob("*.py"):
        for mod in _imported_dotted_modules(path):
            top = mod.split(".")[0]
            if top == "nora":
                # Only an absolute self-import of nora.domain (or a submodule of
                # it) is a sibling import; anything else under the nora
                # namespace — nora.store, nora.config, nora.agent, ... — is
                # exactly the class of import this test exists to catch.
                if mod == "nora.domain" or mod.startswith("nora.domain."):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {mod}")
            elif top not in allowed:
                offenders.append(f"{path.relative_to(ROOT)}: {mod}")
    assert not offenders, f"domain/ may import only the stdlib: {offenders}"
