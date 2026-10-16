"""Structural boundary tests. These are architecture, enforced."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "nora"


def _python_files():
    return [
        p
        for p in PKG.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    ]


def test_doctrine_read_boundary():
    """Only doctrine/loader.py may read doctrine files.

    `skills/loader.py` is exempt: a SKILL.md is NOT a doctrine file — it is the
    parallel on-demand layer (DEC-48), with its own loader for the
    same structural reason doctrine has one, and it reads only its own directory.
    The boundary this test protects (doctrine files have a single reader) is
    untouched by that.

    `instructions/frontmatter.py` is exempt for a third reason: it is the
    GENERIC reader the doctrine's own loader delegates to (the parse/hash
    skeleton, shared so the Dreamer's instruction store does not fork it). It
    knows nothing about doctrine — no kinds, no authority graph, no `<section>`
    rendering — and it is deliberately the ONLY `.md` reader in that package, so
    the boundary this test protects (doctrine files have a single reader) is
    unchanged: `nora/doctrine/` is still reached through `DoctrineStore` alone.

    """
    allowed = {
        PKG / "doctrine" / "loader.py",
        PKG / "skills" / "loader.py",
        PKG / "instructions" / "frontmatter.py",
        # `documents/catalog-prompt.md` is a GENERATED prompt fragment (the catalog a
        # model writes a document against), not a doctrine file; its one reader is
        # `nora.documents.catalog_prompt`, which reads nothing else.
        PKG / "documents" / "__init__.py",
    }
    offenders = []
    for path in _python_files():
        if path in allowed:
            continue
        text = path.read_text()
        if not ("read_text" in text or "open(" in text):
            continue
        # A `.md` in a COMMENT is not a read: a substring match would flag a
        # file whose comment mentions a `.md` next to an unrelated
        # `path.open("rb")` that reads TOML. A gate that cries wolf is a gate
        # somebody switches off (R-29), so the `.md` must appear in a real
        # STRING LITERAL, which is the only form that can name a file to open.
        if any(_md_literals(text)):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"doctrine files read outside loader.py: {offenders}"


def _md_literals(source: str):
    """Every string constant in `source` that names a `.md` file.

    AST rather than substring: comments and docstrings-about-docs are not
    reads. A docstring is itself a `Constant`, so it is excluded explicitly —
    a module whose prose mentions a `.md` path is documentation, not a
    reader. What survives is a literal short enough to be a path or a suffix
    test, which is the shape an actual read has.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:                     # not ours to police here
        return
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and ".md" in node.value and node.value not in docstrings):
            yield node.value


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
#
STRANDS_ALLOWED_PACKAGES = {"agent"}


def test_strands_import_boundary():
    """Only nora/agent/** (Nora's own loop) may import strands."""
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


EVALS_DIR = ROOT / "evals"
#: `subject` is the ONE way `evals/` obtains the composed graph, and the point
#: of it is that no caller assembles one. THIS TEST IS THE GATE THAT BINDS — the
#: import-linter contract "evals may only reach nora.app and nora.testing"
#: reports KEPT while `evals/` imports a package its own name forbids, because
#: `allow_indirect_imports` is load-bearing there (pyproject.toml, that
#: contract's own comment).
#:
#: `domain` is allowed because `evals/backbone.py`'s `render_transcript_at`
#: calls `nora.domain.blocks.render_messages` directly:
#: `nora/domain/__init__.py`'s own docstring is "framework-free contracts,
#: imports only the standard library" — pure types and pure functions, not a
#: collaborator or an assembly seam, so it does not reopen the "no caller
#: assembles a Nora" property `subject` exists to hold.
EVALS_ALLOWED_NORA_PACKAGES = {"app", "testing", "subject", "domain"}


def _evals_files():
    return [p for p in EVALS_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_evals_only_imports_nora_app_and_nora_testing():
    """`evals/` may import `nora.app` and `nora.testing` and nothing else from
    `nora/` ("evals/ may import nora.app and nora.testing — nothing else").
    `pyproject.toml`'s "evals may only reach nora.app and nora.testing"
    import-linter contract enforces the same rule but only over evals' own
    DIRECT imports (`allow_indirect_imports = "True"` — otherwise `nora.app`
    itself legitimately importing `nora.agent`/`nora.doctrine`/etc. internally
    would falsely flag evals for transitively reaching them). This allowlist
    check is the one of the two that also survives a brand new nora subpackage
    being added later, with no edit needed here."""
    offenders = []
    for path in _evals_files():
        for mod in _imported_dotted_modules(path):
            if mod != "nora" and not mod.startswith("nora."):
                continue
            package = mod.split(".", 2)[1] if mod.count(".") >= 1 else None
            if package not in EVALS_ALLOWED_NORA_PACKAGES:
                offenders.append(f"{path.relative_to(ROOT)}: {mod}")
    assert not offenders, f"evals/ may import only nora.app and nora.testing: {offenders}"


def test_evals_never_imports_tests():
    """`evals/` may not import `tests/` at all: `evals/` must not depend on the
    test package."""
    offenders = []
    for path in _evals_files():
        for mod in _imported_dotted_modules(path):
            if mod == "tests" or mod.startswith("tests."):
                offenders.append(f"{path.relative_to(ROOT)}: {mod}")
    assert not offenders, f"evals/ must not import tests/: {offenders}"


def test_domain_imports_only_the_standard_library():
    """domain/ may import the stdlib and its own siblings — nothing else.
    A deny-list only catches names someone thought of; this catches everything.

    A deny-list of hardcoded framework names passes silently on anything not on
    the list — `import aiosqlite` or `from nora.infra.sql import SqlRecordStore`
    would both slip through. An allow-list of "stdlib + own siblings" cannot be
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


def test_composition_never_uses_the_permissive_policy():
    """`nora.policy.permissive_policy` exists for `nora.app.offline` only —
    the eval and integration harnesses drive synthetic conversation ids that no
    real `policy.toml` would list. In production it would silently disable
    deny-by-default: every room allowed, every sender admitted, with a valid
    policy.toml sitting on disk looking authoritative.
    """
    src = (ROOT / "nora" / "app" / "composition.py").read_text()
    assert "permissive_policy" not in src, (
        "composition.py must build the allowlist from policy.toml, never the "
        "permissive one — that would disable deny-by-default in production"
    )


def test_nora_never_imports_evals():
    """The eval layer depends on Nora; Nora depends on nothing in the eval layer.

    The other gates check only the first half:

    - the import-linter `layers` contract orders `nora.*` rows, and `evals` is a
      second root package appearing in no layer;
    - the `evals may only reach ...` contract has `source_modules = ["evals"]`,
      so it constrains `evals -> nora` and not the reverse;
    - `test_evals_only_imports_nora_app_and_nora_testing` scans `evals/` only.

    Without this test, an `import evals...` inserted into a `nora/` module
    leaves `lint-imports` with no broken contract and the rest of the
    architecture suite green: import-linter knows two root packages, the boundary
    test scans one directory.
    """
    offenders = []
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for m in mods:
                if m == "evals" or m.startswith("evals."):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {m}")
    assert not offenders, (
        f"nora imports the eval layer: {offenders}. The direction IS the "
        "property — a Nora that knows it is being measured is not the Nora the "
        "deployment runs."
    )
