"""Structural boundary tests (spec C.1). These are architecture, enforced."""

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
    parallel on-demand layer (design/12 §1, DEC-48), with its own loader for the
    same structural reason doctrine has one, and it reads only its own directory.
    The boundary this test protects (doctrine files have a single reader) is
    untouched by that.

    `instructions/frontmatter.py` is exempt for a third reason: it is the
    GENERIC reader the doctrine's own loader now delegates to (the parse/hash
    skeleton, extracted so the Dreamer's instruction store does not fork it). It
    knows nothing about doctrine — no kinds, no authority graph, no `<section>`
    rendering — and it is deliberately the ONLY `.md` reader in that package, so
    the boundary this test protects (doctrine files have a single reader) is
    unchanged: `nora/doctrine/` is still reached through `DoctrineStore` alone.

    `compute/smith/agent.py` is exempt too, for the same shape of reason:
    `nora/compute/smith/doctrine.md` is SMITH's doctrine, not Nora's (Nora
    Compute spec §3 — Smith is "a separate, neutral agent, not Nora in a
    different mode," with its own persona file read at runtime the same
    convention Nora's own persona already follows). It is read exactly once,
    by Smith's own bootstrap script, inside Smith's own isolated sandbox
    image/environment — never by anything that also touches Nora's doctrine.
    """
    allowed = {
        PKG / "doctrine" / "loader.py",
        PKG / "skills" / "loader.py",
        PKG / "instructions" / "frontmatter.py",
        PKG / "compute" / "smith" / "agent.py",
    }
    offenders = []
    for path in _python_files():
        if path in allowed:
            continue
        text = path.read_text()
        if not ("read_text" in text or "open(" in text):
            continue
        # A `.md` in a COMMENT is not a read. The substring form of this check
        # flagged `nora/config.py` on 2026-09-06 for the sentence
        # «schedule.md §7, 19-between-tiers.md §1» next to an unrelated
        # `path.open("rb")` that reads TOML — a file that touches no doctrine
        # at all. That is the noise CONVENTIONS.md's R-29 story is about: a
        # gate that cries wolf is a gate somebody switches off, and this one
        # sat red on master. So the `.md` must appear in a real STRING
        # LITERAL, which is the only form that can name a file to open.
        if any(_md_literals(text)):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"doctrine files read outside loader.py: {offenders}"


def _md_literals(source: str):
    """Every string constant in `source` that names a `.md` file.

    AST rather than substring: comments and docstrings-about-docs are not
    reads. A docstring is itself a `Constant`, so it is excluded explicitly —
    a module whose prose mentions `design/14.md` is documentation, not a
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
# `compute` is Smith's entire tree (Nora Compute spec §3), not Nora's own
# runtime: `nora/compute/smith/` bakes into a SEPARATE Docker image with its
# own `pyproject.toml` pinning its own `strands-agents==1.50.2` (matched to,
# but not shared with, the top-level `nora` package's copy) -- it never runs
# inside Nora's own process or imports anything else from `nora`
# (`nora/compute/smith/agent.py`'s own module docstring). Allowing the whole
# package, not just that one file, mirrors this test's existing rationale for
# `agent`: Smith growing a second module under `compute/` should not require
# editing this test either.
STRANDS_ALLOWED_PACKAGES = {"agent", "compute"}


def test_strands_import_boundary():
    """Only nora/agent/** (Nora's own loop) and nora/compute/** (Smith's
    separate sandbox image, built and run outside Nora's own process) may
    import strands."""
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
#: `subject` joined 2026-09-17 (design/evaluation/, operator): it is the ONE way
#: `evals/` obtains the composed graph, and the point of it is that no caller
#: assembles one. THIS TEST IS THE GATE THAT BINDS — the import-linter contract
#: "evals may only reach nora.app and nora.testing" reports KEPT while `evals/`
#: imports a package its own name forbids, because `allow_indirect_imports` is
#: load-bearing there (pyproject.toml, that contract's own comment). Measured
#: 2026-09-17 while choosing the layer.
EVALS_ALLOWED_NORA_PACKAGES = {"app", "testing", "subject"}


def _evals_files():
    return [p for p in EVALS_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_evals_only_imports_nora_app_and_nora_testing():
    """`evals/` may import `nora.app` and `nora.testing` and nothing else from
    `nora/` (spec §4.1: "evals/ may import nora.app and nora.testing — nothing
    else"). `pyproject.toml`'s "evals may only reach nora.app and nora.testing"
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
    """`evals/` may not import `tests/` at all — Task 10 closed
    `evals/harness.py`'s `from tests.integration.harness import ScriptedTurn,
    build_handler, group_event`, the coupling that used to leave `evals/`
    dependent on the test package (spec §8); this pins it stays closed."""
    offenders = []
    for path in _evals_files():
        for mod in _imported_dotted_modules(path):
            if mod == "tests" or mod.startswith("tests."):
                offenders.append(f"{path.relative_to(ROOT)}: {mod}")
    assert not offenders, f"evals/ must not import tests/: {offenders}"


def test_domain_imports_only_the_standard_library():
    """domain/ may import the stdlib and its own siblings — nothing else.
    A deny-list only catches names someone thought of; this catches everything.

    Supersedes the old deny-list (`DOMAIN_BANNED`): a deny-list of eight
    hardcoded framework names passes silently on anything not on the list —
    `import aiosqlite` or `from nora.infra.sql import SqlRecordStore` both
    slipped through it with every gate green. An allow-list of "stdlib + own
    siblings" cannot be bypassed by picking an unlisted name.
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

    `nora/policy.py` claimed this test existed before it did. The one-line
    accident it guards against would otherwise ship green.
    """
    src = (ROOT / "nora" / "app" / "composition.py").read_text()
    assert "permissive_policy" not in src, (
        "composition.py must build the allowlist from policy.toml, never the "
        "permissive one — that would disable deny-by-default in production"
    )
