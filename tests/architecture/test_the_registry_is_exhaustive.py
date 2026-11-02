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

#: Imported name -> the ONE block name it always builds. Each lives in its
#: own module (`nora.language.note_render`, `.shared_render`,
#: `nora.domain.decision_log`, `nora.language.culture_render`) and is
#: shared across occasions precisely because it is not parametrised on name.
_SHARED_BUILDER_NAMES = {
    "render_shared_state": "standing",
    "render_notes": "notes",
    "render_decisions": "loop_decisions",
    "render_self": "self",
    # `nora.language.presence_render`: the tag
    # path and the proactive pass render `<presence>` through it, and no
    # occasion builds its own copy.
    "render_presence": "presence",
    # `nora.language.culture_render` (DEC-207): every occasion that carries a
    # room's account renders it through this one builder.
    "render_culture": "culture",
    # `nora.language.note_render.today_block` (context-assembly step 1): the
    # one-line day note-merge, note-candidates and the curator each built for
    # themselves. It always builds `today`, and the caller stamps its row with
    # `restamp`, so this is the same shape as the five above.
    "today_block": "today",
    # `nora.language.profile_render.render_profile` (45 §5): the one `<profile>`
    # block, built for the cognition prompt, the debrief fold and the prefill,
    # and restamped by the caller's own `_BLOCKS` row.
    "render_profile": "profile",
    # `nora.language.character._within_budget`: the one `<social_items>` block,
    # shared by the Character and Profile sessions; its second return value is
    # always that block (or None), restamped by the caller's own row.
    "_within_budget": "social_items",
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
