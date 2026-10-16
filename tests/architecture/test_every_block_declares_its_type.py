"""Every block the model receives has a declared type, and every declared type
is a block the model receives.

**This is the field-level twin of `test_one_block_injector`, and it exists
because that gate stops one level too high.** The injector census proves no
block is hand-written; it cannot see whether the block SAYS anything, because
`ContextBlock` will happily render one with an empty `note=`. The same shape
one layer down: a registry of block types proves nothing on its own if a
renderer can invent a type that is not in it, or if the registry can keep an
entry for a block nothing renders.

So both directions, for the reason
`tests/architecture/test_media_join_reaches_every_transcript.py` gives: listing
catches a rename and misses the thing nobody listed, and "nobody listed it" is
the failure that actually happens.

**What this is guarding against, concretely.** A `ContextBlock(...)` site that
authors its own description inline lets one block type carry TWO descriptions,
one in each module that renders it, with nothing able to notice. A per-type
declaration in `nora/domain/block_types` makes that impossible and this file
keeps it impossible.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from nora.domain.block_types import BLOCK_TYPES

ROOT = Path(__file__).resolve().parents[2]

def _table_names() -> set[str]:
    """The block names in `prefill._BLOCKS` and `_TAG_MESSAGE_BLOCKS` — the
    table the tag path builds from, rather than a literal at a constructor."""
    from nora.agent.prefill import _BLOCKS, _TAG_MESSAGE_BLOCKS

    return {row[0] for row in _BLOCKS} | {row[0] for row in _TAG_MESSAGE_BLOCKS}


def _wrapper_names() -> set[str]:
    """The block names `domain.blocks.wrap_outside_text` renders.

    It builds its `ContextBlock` from a `name` PARAMETER — three callers wrap
    three different kinds of outside text through one helper — so the literal
    is at the CALL SITE, never at the constructor. The AST walk below looks
    only at `ContextBlock(name="…")` and would report all three as
    declared-and-unrendered, which is an argument for deleting three
    descriptions of blocks that ship on every web search.

    First POSITIONAL argument, and there is no default to read: a default would
    let a caller outside transit introduce a fetched web page as a transit
    operator's remark by forgetting to override it. So every name this returns
    is one somebody wrote at a call site, which is also the honest census.

    The same shape as `_table_names`, and the second time this file has needed
    it: a census that reads only constructors is a census of one calling
    convention.
    """
    names: set[str] = set()
    for path in (ROOT / "nora").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func.id if isinstance(node.func, ast.Name) else ""
            # `wrap_outside_text(name, …)` takes it FIRST and positionally;
            # a `*_block(…, name=…)` helper takes it as a keyword. Both build
            # their `ContextBlock` from a parameter, so the literal is at the
            # call site either way.
            found = [
                a.value for a in node.args[:1]
                if fn in {"wrap", "wrap_outside_text"}
                and isinstance(a, ast.Constant) and isinstance(a.value, str)
            ] + [
                kw.value.value for kw in node.keywords
                if fn.endswith("_block") and kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ]
            names.update(found)
    # The debrief's `transcript` and `stretch` are literal
    # `ContextBlock(name=…)` constructors, so the source walk in
    # `_rendered_names` finds both.
    return names


def _literal_block_names(tree: ast.Module) -> set[str]:
    """Every literal `ContextBlock(name="x", ...)` in one parsed module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ContextBlock"
        ):
            for kw in node.keywords:
                if (
                    kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    found.add(kw.value.value)
    return found


def _rendered_names() -> set[str]:
    """Every literal `name=` handed to a `ContextBlock`, discovered from source.

    `prefill` builds its blocks through a `_block(name, …)` helper off a table,
    so the literal is in `_BLOCKS` rather than at the constructor — read from
    there too. Missing that would report `peer_card`, `peer_history` and
    `people_in_window` as DECLARED AND UNRENDERED, arguing for deleting three
    descriptions of blocks that ship on every tag turn.
    """
    found: set[str] = set(_table_names()) | _wrapper_names()
    for path in sorted((ROOT / "nora").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        found |= _literal_block_names(tree)
    return found


def test_every_rendered_block_has_a_declared_type():
    """A block reaching the model with no declared type is a block describing
    itself at inject time, which is the thing the registry replaced."""
    undeclared = _rendered_names() - set(BLOCK_TYPES)
    assert not undeclared, (
        f"{sorted(undeclared)} are rendered as context blocks and declared in "
        "no type. Add each to `nora.domain.block_types.BLOCK_TYPES` with a "
        "description saying what the block IS (a name that is not a block type "
        "does not go through `ContextBlock`)."
    )


def test_a_block_with_no_declared_type_is_caught():
    """Positive control: a synthetic module building a block under a name that
    has no `BlockType` is discovered and undeclared, so the test above fails."""
    tree = ast.parse("b = ContextBlock(name='scratch_unlisted', body='x')\n")
    assert _literal_block_names(tree) == {"scratch_unlisted"}
    assert _literal_block_names(tree) - set(BLOCK_TYPES) == {"scratch_unlisted"}


def test_every_declared_type_is_actually_rendered():
    """The other direction. A type nobody renders is a description that has
    stopped being checked against anything — the stale-catalogue shape this
    registry exists to end, arriving in the registry itself."""
    dead = set(BLOCK_TYPES) - _rendered_names()
    assert not dead, (
        f"{sorted(dead)} are declared types that nothing renders. Delete them: "
        "a description for a block that does not exist is prose no reader will "
        "ever see, and it will be believed by the next person who greps for it."
    )


def test_a_description_describes_rather_than_labels():
    """The bar the module docstring sets: a reader who knows none of our
    vocabulary can act on it.

    Length is a crude proxy and is used as one deliberately — it cannot tell a
    good sentence from a bad one, but it does catch the failure this registry
    is against, which is a LABEL standing in for a description (`untrusted`
    being the one that prompted all of this). A type whose whole description is
    three words has not said what the block is, whose words are in it, or what
    its absence means.
    """
    too_short = {
        name: t.description
        for name, t in BLOCK_TYPES.items()
        if len(t.description.split()) < 8
    }
    assert not too_short, (
        "these describe a block in fewer words than it takes to say what it "
        f"is: {too_short}. A label is what this registry replaced."
    )


def test_no_description_leans_on_a_word_only_we_know():
    """Vocabulary a model has no definition for is the defect that started
    this: an attribute such as `untrusted="true"` defined in one doctrine file,
    loaded by one audience, and rendered to all of them.

    So a description may not lean on our internal nouns. It may NAME a tool she
    can call — that is a thing she has a schema for — but `tier`, `adaptor`,
    `injector` and the rest are ours and mean nothing to her.
    """
    ours = ("tier", "adaptor", "adapter", "injector", "prefill", "dreamer")
    offenders = {
        name: word
        for name, t in BLOCK_TYPES.items()
        for word in ours
        if word in t.description.lower()
    }
    assert not offenders, (
        f"these descriptions use a word only this codebase defines: {offenders}. "
        "Say what the thing IS in her terms, not what we call it."
    )


#: What counts as talking about absence. The trigger has to be as wide as the
#: rule's claim: `"absence"` does not contain `"bsent"`, so a trigger of
#: `"bsent"` alone would never read `<loop_decisions>`'s «its absence says
#: nothing about it». A trigger narrower than its claim is the shape this whole
#: file exists to catch.
_ABSENCE_WORDS = ("absent", "absence", "no block", "nothing is in front of you")


def test_a_description_that_talks_about_ABSENCE_says_the_read_may_have_failed():
    """R-27, checked over every type.

    `assemble` drops a block with an empty body, so every description is read
    on turns where its block is not there. That is fine when it says what
    absence MEANS; it is a LIE when it says «Absent means you have nothing on
    them» and the turn in question never looked. `<people_in_window>` is the
    case: the tag path cannot populate it at all (only the participation
    occasion reads it), so a description claiming a lookup came back empty
    would be false on every tag turn. An un-attempted read rendering as a
    negative result is the ERROR-as-PASS AGENTS.md names, arriving inside a
    prompt.

    So: a description MAY stay silent about absence, and one that mentions it
    MUST carry the hedge.

    The claim lives in the descriptions themselves, which is what lets it be
    checked over all of them at once.
    """
    # «says nothing about it» is `<loop_decisions>`' phrasing and it is the
    # strongest of these — it refuses every conclusion rather than naming the
    # one wrong one.
    hedges = (
        "the read failed", "did not come back", "never that", "never look",
        "it can also mean", "says nothing about it",
        # `<loop_decisions>`' wording: the strongest form in the list, it
        # refuses BOTH readings rather than naming one as wrong.
        "not read an absence as a fact",
    )
    for name, block_type in BLOCK_TYPES.items():
        # PER SENTENCE, not per description. Matching anywhere let a hedge
        # sitting in an unrelated clause authorise a bare «Absent means you
        # have nothing on them» three sentences away — the exact wording this
        # refuses. The claim and its qualifier have to be the same sentence
        # because that is the unit she reads them in.
        for sentence in re.split(r"(?<=\.)\s+", block_type.description):
            if not any(w in sentence.lower() for w in _ABSENCE_WORDS):
                continue
            assert any(h in sentence for h in hedges), (
                f"<{name}> tells her what an ABSENT block means without "
                "saying, IN THE SAME SENTENCE, that the read may simply not "
                "have happened. A turn that never looked must not read as a "
                f"turn that looked and found nothing (R-27). Add one of "
                f"{hedges}:\n  {sentence}"
            )
