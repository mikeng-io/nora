"""Every third-party module `nora/` imports is installed in the deployment image.

The image runs `uv sync --no-dev --extra sentry`, so a module that only a
dev-group package provides passes every test here and fails in production at
import. `tiktoken` did exactly this: step one of Cognition and the
standing ledger imported it, and it was dev-only.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, packages_distributions, requires
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
IMAGE_EXTRAS = ("sentry",)


def _image_distributions() -> set[str]:
    """The declared runtime requirements and everything they pull in."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    specs = list(project["dependencies"])
    for extra in IMAGE_EXTRAS:
        specs += project["optional-dependencies"][extra]
    seen: set[str] = set()
    todo = [Requirement(s) for s in specs]
    while todo:
        req = todo.pop()
        name = canonicalize_name(req.name)
        if name in seen or (req.marker and not req.marker.evaluate({"extra": ""})):
            continue
        seen.add(name)
        try:
            todo += [Requirement(r) for r in requires(req.name) or ()]
        except PackageNotFoundError:
            continue
    return seen


def _type_checking_only(tree: ast.Module) -> set[int]:
    """Line numbers of imports inside `if TYPE_CHECKING:`; they never run."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for inner in node.body:
                lines.update(n.lineno for n in ast.walk(inner) if hasattr(n, "lineno"))
    return lines


def _imported_modules() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted((ROOT / "nora").rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        skip = _type_checking_only(tree)
        for node in ast.walk(tree):
            if getattr(node, "lineno", 0) in skip:
                continue
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top != "nora" and top not in sys.stdlib_module_names:
                    found.setdefault(top, str(path.relative_to(ROOT)))
    return found


def test_every_runtime_import_is_installed_in_the_image() -> None:
    image = _image_distributions()
    providers = packages_distributions()
    missing = {
        module: where
        for module, where in _imported_modules().items()
        if not any(canonicalize_name(d) in image for d in providers.get(module, ()))
    }
    assert missing == {}, f"imported by nora/ but not installed in the image: {missing}"
