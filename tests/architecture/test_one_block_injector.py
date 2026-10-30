"""Every model-facing block reaches the prompt through ONE injector.

**`ContextBlock` is that injector and it has one assembler caller.**
`render()` owns the tag, the `untrusted` marker, the `note=` clause and the
attribute order; `assemble` owns placement and empty-vs-omit; `catalogue`
derives the header's «What you can see:» list from the same declaration so it
cannot go stale. A module that writes its opening tag by hand can ship a block
with no `untrusted="true"` or with no container at all; this file is what keeps
the modules converted.

**Why a static gate, and why it is not the whole story.** The question is «was a
block FORGOTTEN», and a grep-shaped question gets a grep-shaped gate — the same
construction as `test_media_join_reaches_every_transcript`: discover from the
source, then classify, so a new hand-written tag has to be argued for rather
than merely not noticed.

The static half cannot see whether a block that DOES go through `ContextBlock`
carries anything, because `_declared` will happily emit one with `note=""` and
`body=""` purely so the catalogue names it. That is a declaration standing in
for the thing declared, and the runtime half of this gate lives in
`tests/test_prefill.py::test_the_whole_turn_renders_no_unframed_block`, which
computes over every message the turn emits rather than the one `_served_blocks`
happens to read.

There is no list of unconverted modules: a module that opens and closes an
element by hand fails `test_every_module_that_writes_a_tag_is_classified` at
once.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_OPEN = re.compile(r"<([a-z_][a-z0-9_]*)[\s>]")
_CLOSE = re.compile(r"</([a-z_][a-z0-9_]*)>")

#: Has its own injector, the pattern `ContextBlock` follows.
#: `render_section` produces the exact bytes a section contributes and has two
#: consumers — the assembler and the hasher — which is the trick `ContextBlock`
#: borrows for the block and its catalogue line.
_OWN_INJECTOR = {
    "nora/doctrine/loader.py": "`render_section` — doctrine's own injector",
}

#: Tags a module NAMES without BUILDING, with the reason. The discriminator
#: ("opens AND closes in emitted text") cannot tell a strip from a build,
#: because a strip names both tags too.
#:
#: **Scoped per TAG, not per module.** Exempting a module wholesale (say
#: `relevance_gate` for its `<nora_doctrine>` strip) would also excuse every
#: other block in it — a module on `_OWN_INJECTOR` gets no per-module check at
#: all, so the file that exists to stop hand-written blocks would give one
#: module a free pass to grow them. The module is checked and only the tag is
#: allowed.
_NOT_BUILT_HERE = {
    ("nora/ingest/relevance_gate.py", "nora_doctrine"):
        "strips the wrapper doctrine/loader.py builds",
}

def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Every Constant that is a DOCSTRING — prose about a tag, not a tag.

    Without this the scan reads `render_notes`' own docstring arguing about
    `<notes>` as though the module emitted one. The repo's docstrings discuss
    these elements constantly, which is the whole reason the discriminator
    below is «opens AND closes it in emitted text» rather than «mentions it».
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            out.add(id(body[0].value))
    return out


def _built_tags(rel: str) -> tuple[str, ...]:
    """The elements a module OPENS AND CLOSES in text it emits.

    Both halves are required, and that is the discriminator rather than a
    heuristic: prose names `<notes>`; a builder writes `</notes>` too. It also
    keeps `<untrusted source=…>`-style wrappers in and leaves ledger keys like
    `"<memory reads this pass>"` and self-closing markers out, because neither
    has a closing tag anywhere.
    """
    return _tags_in(ast.parse((ROOT / rel).read_text(encoding="utf-8")))


def _tags_in(tree: ast.Module) -> tuple[str, ...]:
    skip = _docstring_nodes(tree)
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            parts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            parts += [
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
    blob = "\n".join(parts)
    return tuple(sorted(set(_OPEN.findall(blob)) & set(_CLOSE.findall(blob))))


def _discovered() -> dict[str, tuple[str, ...]]:
    out = {}
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "nora/domain/blocks.py":
            continue
        try:
            tags = _built_tags(rel)
        except SyntaxError:                                # pragma: no cover
            continue
        if tags:
            out[rel] = tags
    return out


_CLASSIFIED = set(_OWN_INJECTOR)


def _unclassified(discovered: dict[str, tuple[str, ...]]) -> set[str]:
    # `CONVERTED` counts as classified: those modules ARE checked, per module,
    # by `test_no_block_is_written_by_hand`. Leaving them out of `_CLASSIFIED`
    # would make the census demand a second classification for a module the stricter
    # test already covers.
    return set(discovered) - _CLASSIFIED - set(CONVERTED)


def test_every_module_that_writes_a_tag_is_classified():
    """The discovery step. A new hand-written block appears here and has to be
    argued for — it cannot merely go unnoticed, which is the only failure mode
    that has actually happened."""
    discovered = _discovered()
    unclassified = _unclassified(discovered)
    assert not unclassified, (
        f"{sorted(unclassified)} open and close an element by hand and are "
        "classified nowhere. Route it through ContextBlock (see #292), or "
        "classify it in _OWN_INJECTOR with a reason."
    )
    stale = _CLASSIFIED - set(discovered)
    assert not stale, (
        f"{sorted(stale)} are classified here and write no tag any more. "
        "Delete their lines: a classification for code that is gone widens "
        "the gate silently."
    )


def test_a_module_that_writes_a_tag_by_hand_is_caught():
    """Positive control for the census: a synthetic module that opens and
    closes an element in emitted text is discovered and unclassified, so the
    test above would fail on it. Prose that only names the tag is not."""
    offender = ast.parse("def build(x):\n    return f'<facts>{x}</facts>'\n")
    prose = ast.parse('"""Mentions <facts> only."""\nx = "<facts>"\n')
    assert _tags_in(offender) == ("facts",)
    assert _tags_in(prose) == ()
    assert _unclassified({"nora/scratch.py": ("facts",)}) == {"nora/scratch.py"}


#: **The modules that build blocks through `ContextBlock`**, named so the
#: per-module guard below has something to iterate (a test parametrised over an
#: empty dict COLLECTS NOTHING: pytest emits one skipped `[NOTSET]` case and the
#: file reads as though a gate is running when none is, which is this file's own
#: subject, a declaration that reads as a guarantee and enforces nothing).
#: `test_the_converted_list_is_not_quietly_shrinking` checks the list against the
#: source, so a module that builds blocks and is missing here fails.
CONVERTED = (
    "nora/language/comprehend_render.py",
    "nora/agent/standing_ledger.py",
    "nora/app/extraction.py",
    "nora/app/profile_spike.py",
    "nora/agent/prefill.py",
    "nora/agent/reaction.py",
    "nora/agent/speech_format.py",
    "nora/domain/decision_log.py",
    "nora/ingest/relevance_gate.py",
    "nora/language/computer_render.py",
    "nora/language/documents_render.py",
    "nora/language/subagents_render.py",
    "nora/language/checkpoint_render.py",
    "nora/language/culture_render.py",
    "nora/language/note_render.py",
    "nora/language/memo_render.py",
    "nora/language/presence_render.py",
    "nora/language/standing_render.py",
    "nora/engagement/debrief_render.py",
    "nora/infra/transit/arrivals.py",
    "nora/language/cognition_render.py",
    "nora/language/confirmation_render.py",
    "nora/language/subagent_render.py",
    "nora/language/culture.py",
    "nora/language/character.py",
    "nora/language/profile.py",
    "nora/language/profile_render.py",
    "nora/language/profile_collective.py",
    "nora/language/profile_verdict.py",
    "nora/language/lessons.py",
    "nora/language/lesson_render.py",
    "nora/language/doctrine_check.py",
    "nora/domain/summons.py",
    "nora/skills/loader.py",
    "nora/subject/context_filter.py",
)


@pytest.mark.parametrize("rel", CONVERTED)
def test_no_block_is_written_by_hand(rel: str):
    """THE GATE, per module. One injector, so `untrusted`, the framing clause,
    the order and the catalogue line stop being per-site decisions.

    `test_every_module_that_writes_a_tag_is_classified` is the half that catches
    a module NOT on this list; this is the half that catches one on it
    regressing, and it names which.
    """
    hand_written = tuple(
        tag for tag in _built_tags(rel)
        if (rel, tag) not in _NOT_BUILT_HERE
    )
    assert not hand_written, (
        f"{rel} opens {list(hand_written)} by hand instead of through "
        "ContextBlock.render()"
    )


def test_the_converted_list_is_not_quietly_shrinking():
    """`CONVERTED` is a hand-written list, which goes
    stale — so it is checked against the source rather than trusted. Every
    module that builds a prompt block through `ContextBlock` must be on it, or
    dropping a module's line here would silently stop guarding it."""
    builds = set(CONVERTED) | set(_OWN_INJECTOR)
    uses_injector = set()
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        # AST, not a substring. `"ContextBlock(" in source` also matches PROSE
        # (a docstring quoting a `ContextBlock(...)` call-site count), and the
        # substring check would read that as a module building blocks. Same
        # defect as a gate satisfied by its own documentation, arriving from the
        # false-POSITIVE side.
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "ContextBlock"
            for n in ast.walk(tree)
        ):
            uses_injector.add(rel)
    missing = uses_injector - builds - {"nora/domain/blocks.py"}
    assert not missing, (
        f"{sorted(missing)} build blocks through ContextBlock and are not in "
        "CONVERTED, so nothing checks that they stay hand-written-tag-free."
    )
