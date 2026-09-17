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
the only failure that has actually happened here. Five times, per
`catalogue()`'s own docstring.

**What this is guarding against, concretely.** Before `nora/domain/block_types`
existed, 27 of 39 `ContextBlock(...)` sites authored their own description
inline, and four block types carried TWO — `room`, `standing`, `batch` and
`now` each described itself one way in one module and another way in another,
with nothing able to notice. That is what a per-type declaration makes
impossible and what this file keeps impossible.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nora.domain.block_types import BLOCK_TYPES

ROOT = Path(__file__).resolve().parents[2]

#: Names passed to `ContextBlock(name=…)` that are NOT block types, with the
#: reason. Kept as an inverted allowlist so a new name has to be argued for.
_NOT_A_BLOCK_TYPE: dict[str, str] = {
    # THE LEGENDS, and they are on their way out rather than exempt forever.
    # `how_to_read_the_transcript` and `how_to_read_reasoning` exist ONLY
    # because there was nowhere to say what a field means — they are a block
    # whose whole content is an annotation, written as prose beside the block
    # it annotates. Once the notation is derived from `BlockType.annotations`,
    # they have no content left and go.
    "how_to_read_the_transcript": "a legend; becomes derived annotation",
    "how_to_read_reasoning": "a legend; becomes derived annotation",
    # The catalogue's wrapper. Deleted with the catalogue (#314) — it is the
    # envelope around «What you can see:», which is the second copy of what
    # every block's own description already says.
    "context_header": "wraps the catalogue, which is being deleted",
}


def _table_names() -> set[str]:
    """The block names in `prefill._BLOCKS` and `_TAG_MESSAGE_BLOCKS` — the
    table the tag path builds from, rather than a literal at a constructor."""
    from nora.agent.prefill import _BLOCKS, _TAG_MESSAGE_BLOCKS

    return {row[0] for row in _BLOCKS} | {row[0] for row in _TAG_MESSAGE_BLOCKS}


def _rendered_names() -> set[str]:
    """Every literal `name=` handed to a `ContextBlock`, discovered from source.

    `prefill` builds its blocks through a `_block(name, …)` helper off a table,
    so the literal is in `_BLOCKS` rather than at the constructor — read from
    there too. Missing that was worth catching: the first version of this file
    reported `peer_card`, `peer_history` and `people_in_window` as DECLARED AND
    UNRENDERED, which would have argued for deleting three descriptions of
    blocks that ship on every tag turn.
    """
    found: set[str] = set(_table_names())
    for path in sorted((ROOT / "nora").rglob("*.py")):
        if "compute/smith" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
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


def test_every_rendered_block_has_a_declared_type():
    """A block reaching the model with no declared type is a block describing
    itself at inject time, which is the thing the registry replaced."""
    undeclared = _rendered_names() - set(BLOCK_TYPES) - set(_NOT_A_BLOCK_TYPE)
    assert not undeclared, (
        f"{sorted(undeclared)} are rendered as context blocks and declared in "
        "no type. Add each to `nora.domain.block_types.BLOCK_TYPES` with a "
        "description saying what the block IS, or classify it in "
        "`_NOT_A_BLOCK_TYPE` with a reason."
    )


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
    this (#314): `untrusted="true"` was defined in exactly one doctrine file,
    loaded by one of six audiences, and rendered to all six.

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
