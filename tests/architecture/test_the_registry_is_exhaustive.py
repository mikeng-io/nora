"""`design/context-assembly/01-the-registry.md` S1's acceptance criterion #1:
"A test walks the registry and asserts no block is built outside it." This is
that test, for the five occasions declared during S1
(`design/context-assembly/03-sequencing.md`) — `nora.agent.prefill` already
has a STRONGER version of this bar (`tests/test_prefill.py`'s declared-vs-
rendered-in-a-full-fixture pair), so it is not repeated here.

**AST, not import — same reason `test_one_block_injector.py` reads source
rather than running the module: a census that has to construct each
occasion's real dependencies to import it is a census nobody runs.** Two
things are counted per module: every literal `ContextBlock(name="x", ...)`,
and every call to one of four KNOWN shared builders
(`render_notes`/`render_standing`/`render_decisions`/`render_self_card`),
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

#: The five occasions this bundle declared in S1. `nora/agent/prefill.py` is
#: deliberately absent — see the module docstring.
_OCCASIONS = (
    "nora/ingest/relevance_gate.py",
    "nora/language/proactive.py",
    "nora/language/note_curator.py",
    "nora/language/note_candidates.py",
    "nora/language/culture.py",
)

#: Imported name -> the ONE block name it always builds. Each lives in its
#: own module (`nora.language.note_render`, `.standing_render`,
#: `nora.domain.decision_log`, `nora.language.self_card_render`) and is
#: shared across occasions precisely because it is not parametrised on name.
_SHARED_BUILDER_NAMES = {
    "render_notes": "notes",
    "render_standing": "standing",
    "render_decisions": "loop_decisions",
    "render_self_card": "self_card",
    # `nora.language.presence_render` (design/self-awareness/02): the tag
    # path and the proactive pass render `<presence>` through it, and no
    # occasion builds its own copy.
    "render_presence": "presence",
    # `nora.language.culture_render` (DEC-204): every occasion that carries a
    # room's account renders it through this one builder.
    "render_culture": "culture",
}


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
    """Every call to one of the four known shared builders, resolved through
    the module's OWN imports rather than matched on the bare call text — an
    import makes the alias this module calls it by, and a call under any
    other name (a local function that happens to share the string) is not
    counted."""
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in _SHARED_BUILDER_NAMES
    }
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in imported
        ):
            names.add(_SHARED_BUILDER_NAMES[node.func.id])
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
