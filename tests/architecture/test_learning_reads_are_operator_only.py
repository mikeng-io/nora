"""Keep learning measures off agent, engagement and memory tool paths."""

import ast
from importlib.util import resolve_name
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_READER = "nora.memory.adapters.learning_reads"
_CALLERS = ("nora/agent", "nora/engagement", "nora/memory/adapters/tools")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package = ".".join(path.relative_to(_ROOT).parts[:-1])
                module = resolve_name("." * node.level + module, package)
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_learning_reads_are_operator_only() -> None:
    offenders = [
        path.relative_to(_ROOT)
        for caller in _CALLERS
        for path in (_ROOT / caller).rglob("*.py")
        if _READER in _imports(path)
    ]
    assert not offenders
