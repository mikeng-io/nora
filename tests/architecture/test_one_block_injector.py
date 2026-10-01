"""Every model-facing block reaches the prompt through ONE injector.

**`ContextBlock` is that injector and it has one assembler caller.** #220 built
it — `render()` owns the tag, the `untrusted` marker, the `note=` clause and the
attribute order; `assemble` owns placement and empty-vs-omit; `catalogue` derives
the header's «What you can see:» list from the same declaration so it cannot go
stale a fifth time. `build_prefill` used all three and nine other modules wrote
their opening tags by hand — which is how `<open_notes>` shipped with no
`untrusted="true"` and the note curator's `<note>` elements shipped with no
container at all. #292 converted all nine; this file is what keeps them
converted.

**Why a static gate, and why it is not the whole story.** The question is «was a
block FORGOTTEN», and a grep-shaped question gets a grep-shaped gate — the same
construction as `test_media_join_reaches_every_transcript`: discover from the
source, then classify, so a new hand-written tag has to be argued for rather
than merely not noticed.

The static half cannot see whether a block that DOES go through `ContextBlock`
carries anything, because `_declared` will happily emit one with `note=""` and
`body=""` purely so the catalogue names it — which is what `replying_to` and
`tag_message` WERE until #292 gave them real ones. That is #187's pattern, a
declaration standing in for the thing declared, and the runtime half of this
gate lives in
`tests/test_prefill.py::test_the_whole_turn_renders_no_unframed_block`, which
computes over every message the turn emits rather than the one `_served_blocks`
happens to read.

Issue #292. `HAND_WRITTEN` empty is the finish line.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_OPEN = re.compile(r"<([a-z_][a-z0-9_]*)[\s>]")
_CLOSE = re.compile(r"</([a-z_][a-z0-9_]*)>")

#: **EMPTY — the finish line #292 was opened for.** Module -> the elements it
#: opens by hand. Nine modules were on it; all nine now build every model-facing
#: block through `ContextBlock`, so `untrusted`, the framing clause and the
#: attribute order are properties of the type rather than things each site
#: remembered to type.
#:
#: The tests below still run: `test_every_module_that_writes_a_tag_is_classified`
#: fails on a NEW hand-written block, and `test_the_census_can_only_shrink`
#: cannot be widened by adding one here without a reason beside it. Re-populating
#: this dict is how the tree gets back to where it was, so an entry needs an
#: argument, not just a line.
#:
HAND_WRITTEN: dict[str, tuple[str, ...]] = {}

#: Has its own injector, and it is the one #220 took the pattern FROM.
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
#: **Scoped per TAG, not per module, and that is the fix for a hole this file
#: had for one commit.** `relevance_gate` was exempted wholesale for its
#: `<nora_doctrine>` strip, which also excused every other block in it — a
#: module on `_OWN_INJECTOR` gets no per-module check at all, so the file that
#: exists to stop hand-written blocks was giving one module a free pass to grow
#: them. Now the module is checked and only the tag is allowed.
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
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
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


_CLASSIFIED = set(HAND_WRITTEN) | set(_OWN_INJECTOR)


def test_every_module_that_writes_a_tag_is_classified():
    """The discovery step. A new hand-written block appears here and has to be
    argued for — it cannot merely go unnoticed, which is the only failure mode
    that has actually happened."""
    discovered = _discovered()
    # `CONVERTED` counts as classified: those modules ARE checked, per module,
    # by `test_no_block_is_written_by_hand`. Leaving them out of `_CLASSIFIED`
    # made this test demand a second classification for a module the stricter
    # test already covers.
    unclassified = set(discovered) - _CLASSIFIED - set(CONVERTED)
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


def test_the_census_can_only_shrink():
    """A module still on the census may not grow a NEW hand-written element.

    The `xfail` below says «this module has not been converted yet». It must
    not also say «and anything added to it meanwhile is fine» — that is how a
    pinned baseline becomes a budget. Recorded tags may disappear; new ones
    fail here even while the module's own row is still expected to fail.
    """
    discovered = _discovered()
    for rel, recorded in sorted(HAND_WRITTEN.items()):
        added = set(discovered.get(rel, ())) - set(recorded)
        assert not added, (
            f"{rel} grew hand-written element(s) {sorted(added)} while still "
            "on #292's census. New model-facing blocks go through ContextBlock."
        )


#: **The nine modules #292 converted**, named so the guard below has something
#: to iterate. `HAND_WRITTEN` is empty, and a test parametrised over an empty
#: dict COLLECTS NOTHING — pytest emits a single skipped `[NOTSET]` case and the
#: file reads as though a per-module gate is running when none is.
#:
#: That is this file's own subject arriving in this file: a declaration that
#: reads as a guarantee and enforces nothing (#187). Caught by a peer asking
#: whether anything that is not the real declaration could satisfy the check,
#: and by collecting the tests to look rather than trusting the green.
#: Found by `test_the_converted_list_is_not_quietly_shrinking` on its first run:
#: the four `*_render.py` modules and `decision_log` build `ContextBlock`s and
#: were never on `HAND_WRITTEN` — they never hand-wrote a tag — so nothing was
#: checking that they never start.
CONVERTED = (
    "nora/agent/prefill.py",
    "nora/agent/reaction.py",
    "nora/agent/speech_format.py",
    "nora/domain/decision_log.py",
    "nora/ingest/relevance_gate.py",
    "nora/language/computer_render.py",
    "nora/language/documents_render.py",
    "nora/language/culture_render.py",
    "nora/language/note_render.py",
    "nora/language/presence_render.py",
    "nora/language/self_card_render.py",
    "nora/language/standing_render.py",
    "nora/engagement/debrief_render.py",
    "nora/infra/transit/arrivals.py",
    "nora/language/cognition_render.py",
    "nora/language/culture.py",
    "nora/language/note_candidates.py",
    "nora/language/note_curator.py",
    "nora/language/note_merge.py",
    "nora/language/proactive.py",
    "nora/domain/summons.py",
    "nora/skills/loader.py",
)


@pytest.mark.parametrize("rel", CONVERTED)
def test_no_block_is_written_by_hand(rel: str):
    """THE GATE, per module. One injector, so `untrusted`, the framing clause,
    the order and the catalogue line stop being per-site decisions.

    Named modules rather than `HAND_WRITTEN`'s keys, so this keeps asserting
    after the census empties — which is the moment it would otherwise go quiet.
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
    """`CONVERTED` is a hand-written list, which this repo's history says goes
    stale — so it is checked against the source rather than trusted. Every
    module that builds a prompt block through `ContextBlock` must be on it, or
    dropping a module's line here would silently stop guarding it."""
    builds = set(CONVERTED) | set(_OWN_INJECTOR)
    uses_injector = set()
    for path in sorted((ROOT / "nora").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        # AST, not a substring. `"ContextBlock(" in source` also matches PROSE:
        # `nora/domain/block_types.py`'s docstring says «27 of 39
        # `ContextBlock(...)` call sites», and the substring check read that as
        # a module building blocks. Same defect as a gate satisfied by its own
        # documentation, arriving from the false-POSITIVE side.
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
