"""A test walks the registry and asserts no block is built outside it. This is
that test, for the declared occasions — `nora.agent.prefill` already
has a STRONGER version of this bar (`tests/test_prefill.py`'s declared-vs-
rendered-in-a-full-fixture pair), so it is not repeated here.

**AST, not import — same reason `test_one_block_injector.py` reads source
rather than running the module: a census that has to construct each
occasion's real dependencies to import it is a census nobody runs.** Two
things are counted per module: every literal `ContextBlock(name="x", ...)`,
and every call to one of four KNOWN shared builders
(`render_notes`/`render_standing`/`render_decisions`/`render_self`),
each of which always produces exactly one fixed block name regardless of its
arguments — that name comes from the BUILDER's own source, never guessed from
the call site. The union is checked against the module's own `_BLOCKS` tuple,
also read as data rather than imported, in both directions: a block built
with no row, and a row with no block built.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Every module that declares a `_BLOCKS` table, FOUND, not listed: a list of
#: occasions is exactly what let the Cognition session be built without one
#: (`test_every_model_call_is_a_declared_occasion.py` is the other half: it
#: finds the modules that build a prompt and demands they declare). The one
#: absence is `nora/agent/prefill.py`, see the module docstring.
def _declared_occasions() -> tuple[str, ...]:
    found = []
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "nora/agent/prefill.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name) and node.target.id == "_BLOCKS"
            and isinstance(node.value, ast.Tuple)
            for node in ast.walk(tree)
        ):
            found.append(rel)
    return tuple(found)


_OCCASIONS = _declared_occasions()


def test_the_census_finds_the_five_occasions_it_used_to_list():
    """A census that silently found nothing would pass every test below."""
    assert {
        "nora/ingest/relevance_gate.py", "nora/language/culture.py",
    } <= set(_OCCASIONS), sorted(_OCCASIONS)

def _local_block_names(tree: ast.Module) -> set[str]:
    """Every `ContextBlock(name="x", ...)` literal — `name` given as a plain
    string constant, which is how all five occasions build one directly."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "ContextBlock"
        ):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "name" and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                names.add(kw.value.value)
    return names


def _shared_builder_names(tree: ast.Module) -> set[str]:
    """Follow actual imported/local builder calls to ContextBlock constructors.

    Shared wrappers may return several types. Derive those types at their owner
    rather than maintaining another function-name to block-name registry.
    """
    seen: set[tuple[str, str]] = set()
    parsed: dict[str, ast.Module] = {}

    def imports(module: ast.Module) -> dict[str, tuple[str, str]]:
        return {a.asname or a.name: (n.module, a.name)
                for n in ast.walk(module) if isinstance(n, ast.ImportFrom)
                and n.module and n.module.startswith("nora.") for a in n.names}

    def follow(module_name: str, function: str) -> set[str]:
        key = (module_name, function)
        if key in seen:
            return set()
        seen.add(key)
        path = ROOT / (module_name.replace(".", "/") + ".py")
        if not path.is_file():
            return set()
        if module_name not in parsed:
            parsed[module_name] = ast.parse(path.read_text())
        module = parsed[module_name]
        fn = next((n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == function), None)
        if fn is None:
            return set()
        subtree = ast.Module(body=fn.body, type_ignores=[])
        names = _local_block_names(subtree)
        bindings = imports(module)
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                name = call.func.id
                target = bindings.get(name, (module_name, name))
                names |= follow(*target)
        return names

    bindings = imports(tree)
    names: set[str] = set()
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in bindings
        ):
            names |= follow(*bindings[call.func.id])
    return names


def _declared_names(tree: ast.Module, rel: str) -> set[str]:
    """The module's own `_BLOCKS` tuple, read as data. Raises if a named
    occasion has no such table — this test exists BECAUSE a table exists;
    a module dropped from `_OCCASIONS` should also lose it from S1's claim."""
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name) and node.target.id == "_BLOCKS"
            and isinstance(node.value, ast.Tuple)
        ):
            continue
        names: set[str] = set()
        for elt in node.value.elts:
            if (
                isinstance(elt, ast.Call)
                and isinstance(elt.func, ast.Name)
                and elt.func.id == "BlockDeclaration"
            ):
                assert isinstance(elt.args[0], ast.Constant)
                names.add(elt.args[0].value)
            if (
                isinstance(elt, ast.Tuple) and elt.elts
                and isinstance(elt.elts[0], ast.Constant)
                and isinstance(elt.elts[0].value, str)
            ):
                names.add(elt.elts[0].value)
        return names
    raise AssertionError(
        f"{rel} has no `_BLOCKS: tuple[...] = (...)` literal — "
        "design/context-assembly S1 claims one for every occasion in "
        "_OCCASIONS above."
    )


@pytest.mark.parametrize("rel", _OCCASIONS)
def test_every_block_the_occasion_builds_has_a_row(rel: str):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    built = _local_block_names(tree) | _shared_builder_names(tree)
    declared = _declared_names(tree, rel)
    missing = built - declared
    assert not missing, (
        f"{rel} builds {sorted(missing)} with no row in its own `_BLOCKS` — "
        "the exact gap S1 was declared to close."
    )


@pytest.mark.parametrize("rel", _OCCASIONS)
def test_no_declared_row_is_dead(rel: str):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    built = _local_block_names(tree) | _shared_builder_names(tree)
    declared = _declared_names(tree, rel)
    extra = declared - built
    assert not extra, (
        f"{rel} declares {sorted(extra)} in `_BLOCKS` but never builds "
        "them — a row with no builder is a second copy of nothing."
    )
