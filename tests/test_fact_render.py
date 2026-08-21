from __future__ import annotations

from nora.domain.facts import Fact
from nora.language.fact_render import FACTS_BLOCK_NOTE, render_facts

DIGEST = "a" * 64
MODEL = "ollamacloud/deepseek-v4-flash"


def _fact(obj="black_tower", excerpt="Red, Green, Yellow, Blue, and Black towers"):
    return Fact(
        id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
        subject_id="Big Walk", predicate="has", object_entity_id=None,
        object_literal=obj, source_url="https://keengamer.test/w",
        source_excerpt=excerpt, valid_from=1, created_at=1,
        instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
    )


def test_an_empty_tier_renders_no_block_at_all():
    """`RoomLanguage.__bool__`'s rule: empty is a REAL state, and the renderer
    emits nothing rather than an empty block. This is what lets the tier ship
    dormant and be invisible until something writes to it."""
    assert render_facts(()) == ""


def test_the_note_does_not_claim_the_room_produced_this():
    """`ROOM_BLOCK_NOTE` says «distilled from this room's own messages», which
    is FALSE for a workspace-keyed block — and it would be false in the one
    place the model is being asked to trust a sentence."""
    assert "this room's own messages" not in FACTS_BLOCK_NOTE
    assert "instruction to you" in FACTS_BLOCK_NOTE


def test_the_block_carries_the_source_with_the_claim():
    """A fact without its warrant is exactly the conclusion-outliving-its-
    evidence shape the tier exists to end."""
    out = render_facts((_fact(),))
    assert "keengamer.test" in out
    assert "black_tower" in out


def test_everything_group_derived_is_escaped():
    out = render_facts((_fact(obj="<injected>", excerpt="a & b"),))
    assert "<injected>" not in out
    assert "&lt;injected&gt;" in out
    assert "&amp;" in out


def test_a_superseded_fact_is_never_rendered():
    superseded = Fact(
        id="0191f2c4-0000-7000-8000-000000000002", workspace="nora",
        subject_id="Big Walk", predicate="has", object_entity_id=None,
        object_literal="gone", source_url="https://x.test/a",
        source_excerpt="x", valid_from=1, created_at=1,
        instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
        superseded_at=5, superseded_by="0191f2c4-0000-7000-8000-000000000001",
        supersede_reason="stale",
    )
    assert render_facts((superseded,)) == ""


def test_the_opening_tag_carries_the_note():
    """The note belongs in the OPENING TAG, not just somewhere in the block —
    same ruling as every sibling block (`ROOM_BLOCK_NOTE`'s own docstring): an
    attribute is the one position rendered content can never reach, and it is
    read before the body. A regression that moved the note into the body text
    (still true, but no longer load-bearing at the one un-spoofable position)
    would pass every other test in this file."""
    out = render_facts((_fact(),))
    opening_tag = out[: out.index(">") + 1]
    assert FACTS_BLOCK_NOTE in opening_tag
    assert 'untrusted="true"' in opening_tag


def test_a_mix_of_standing_and_superseded_keeps_only_the_standing_one():
    """A single-fact input can pass the superseded test by coincidence (an
    empty-tuple bug and an unfiltered-list bug both happen to satisfy it
    differently); a MIX is what actually forces the implementation to filter
    per-fact rather than short-circuit on the whole tuple."""
    standing = _fact(obj="black_tower")
    superseded = Fact(
        id="0191f2c4-0000-7000-8000-000000000002", workspace="nora",
        subject_id="Big Walk", predicate="has", object_entity_id=None,
        object_literal="gone", source_url="https://x.test/a",
        source_excerpt="x", valid_from=1, created_at=1,
        instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
        superseded_at=5, superseded_by="0191f2c4-0000-7000-8000-000000000001",
        supersede_reason="stale",
    )
    out = render_facts((superseded, standing))
    assert "gone" not in out
    assert "black_tower" in out
