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
import re
from pathlib import Path

from nora.domain.block_types import BLOCK_TYPES

ROOT = Path(__file__).resolve().parents[2]

#: Names passed to `ContextBlock(name=…)` that are NOT block types, with the
#: reason. Kept as an inverted allowlist so a new name has to be argued for.
#:
#: **It is EMPTY, and it stays here for that reason.** Its two entries were the
#: legends — `how_to_read_the_transcript` and `how_to_read_reasoning`, blocks
#: whose whole content was prose describing a sibling's fields, which existed
#: only because a field's meaning had nowhere to live. `BlockType.annotations`
#: is where it lives, so both were deleted rather than exempted.
#:
#: An empty dict is the honest state of an inverted allowlist that has nothing
#: to exempt. Deleting it would make the next exemption a one-line edit with no
#: place to write the reason, which is how the list this file replaces grew.
_NOT_A_BLOCK_TYPE: dict[str, str] = {}


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
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"wrap", "wrap_outside_text"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
    return names


def _rendered_names() -> set[str]:
    """Every literal `name=` handed to a `ContextBlock`, discovered from source.

    `prefill` builds its blocks through a `_block(name, …)` helper off a table,
    so the literal is in `_BLOCKS` rather than at the constructor — read from
    there too. Missing that was worth catching: the first version of this file
    reported `peer_card`, `peer_history` and `people_in_window` as DECLARED AND
    UNRENDERED, which would have argued for deleting three descriptions of
    blocks that ship on every tag turn.
    """
    found: set[str] = set(_table_names()) | _wrapper_names()
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


#: What counts as talking about absence. **Measured, 2026-09-17: the first cut
#: of this was `"bsent" or "No block"` and it inspected FOUR of 35 descriptions.
#: `"absence"` does not contain `"bsent"`, so `<loop_decisions>`'s «its absence
#: says nothing about it» was never read at all** — and the ADR claimed this
#: rule now applied to every type. A trigger narrower than its claim is the
#: shape this whole file exists to catch, arriving in the file itself.
_ABSENCE_WORDS = ("absent", "absence", "no block", "nothing is in front of you")


def test_a_description_that_talks_about_ABSENCE_says_the_read_may_have_failed():
    """R-27, moved here from the catalogue and widened to every type.

    `assemble` drops a block with an empty body, so every description is read
    on turns where its block is not there. That is fine when it says what
    absence MEANS; it is a LIE when it says «Absent means you have nothing on
    them» and the turn in question never looked. `<people_in_window>` shipped
    exactly that for one commit: the tag path cannot populate it at all (only
    the participation occasion reads it), so on every tag turn the model was
    told a lookup had come back empty when no lookup had been attempted. An
    un-attempted read rendering as a negative result is the ERROR-as-PASS
    AGENTS.md names, arriving inside a prompt.

    So: a description MAY stay silent about absence, and one that mentions it
    MUST carry the hedge.

    **It used to live in `tests/test_prefill.py` and it covered eight blocks —
    the ones with a catalogue line.** The catalogue is gone and the claim moved
    into the descriptions themselves (#314), which is what lets it be checked
    over all of them at once. That is the second time this branch has found the
    registry testing something the hand-written list could only test a subset
    of; the first was `test_every_rendered_block_has_a_declared_type`.
    """
    # «says nothing about it» is `<loop_decisions>`' phrasing and it is the
    # strongest of these — it refuses every conclusion rather than naming the
    # one wrong one. Added when widening the trigger above surfaced it, which
    # is the widening working: the sentence had been correct and unread.
    hedges = (
        "the read failed", "did not come back", "never that", "never look",
        "it can also mean", "says nothing about it",
    )
    for name, block_type in BLOCK_TYPES.items():
        # PER SENTENCE, not per description. Matching anywhere let a hedge
        # sitting in an unrelated clause authorise a bare «Absent means you
        # have nothing on them» three sentences away — the exact wording this
        # refuses, so the first version of it could be satisfied by the bug.
        # The claim and its qualifier have to be the same sentence because that
        # is the unit she reads them in.
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
