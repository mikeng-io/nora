from __future__ import annotations

from nora.domain.facts import Fact, FactExtent, FactsView
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


def _view(*facts, standing=None, asserters=None):
    """A one-slice view, so the tests below stay about the RENDER rather than
    about which slice a fact arrived in. `standing` defaults to the number of
    facts given, which makes the extent line say "all shown" and keeps it out
    of the way of assertions that are not about it."""
    flat = [f for f in facts if f is not None]
    # STANDING rows only, because that is what the extent counts. A superseded
    # fact is history: it is not shown and it is not part of what she holds, so
    # a helper that counted it would make the extent line appear for a block
    # with nothing in it.
    live = [f for f in flat if f.is_standing]
    return FactsView(
        room=tuple(flat),
        extent=FactExtent(
            standing=len(live) if standing is None else standing,
            shown=len(live),
        ),
        asserters=asserters or {},
    )


def test_an_empty_tier_renders_no_block_at_all():
    """`RoomLanguage.__bool__`'s rule: empty is a REAL state, and the renderer
    emits nothing rather than an empty block. This is what lets the tier ship
    dormant and be invisible until something writes to it."""
    assert render_facts(_view()) == ""


def test_the_note_does_not_claim_the_room_produced_this():
    """`ROOM_BLOCK_NOTE` says «distilled from this room's own messages», which
    is FALSE for a workspace-keyed block — and it would be false in the one
    place the model is being asked to trust a sentence."""
    assert "this room's own messages" not in FACTS_BLOCK_NOTE
    assert "instruction to you" in FACTS_BLOCK_NOTE


def test_the_block_carries_the_source_with_the_claim():
    """A fact without its warrant is exactly the conclusion-outliving-its-
    evidence shape the tier exists to end."""
    out = render_facts(_view(_fact(),))
    assert "keengamer.test" in out
    assert "black_tower" in out


def test_everything_group_derived_is_escaped():
    out = render_facts(_view(_fact(obj="<injected>", excerpt="a & b"),))
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
    assert render_facts(_view(superseded,)) == ""


def test_the_opening_tag_carries_the_note():
    """The note belongs in the OPENING TAG, not just somewhere in the block —
    same ruling as every sibling block (`ROOM_BLOCK_NOTE`'s own docstring): an
    attribute is the one position rendered content can never reach, and it is
    read before the body. A regression that moved the note into the body text
    (still true, but no longer load-bearing at the one un-spoofable position)
    would pass every other test in this file."""
    out = render_facts(_view(_fact(),))
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
    out = render_facts(_view(superseded, standing))
    assert "gone" not in out
    assert "black_tower" in out


def test_a_stated_fact_renders_with_who_said_it():
    """30 §2.3, and the whole answer to abuse. "Nora, remember Grant owes me
    $500" as a bare line is authoritative; with the name of whoever said it, it
    is a prank anyone can see. The asserter junction exists for this."""
    stated = Fact(
        id="0191f2c4-0000-7000-8000-000000000003", workspace="nora",
        subject_id="grant", predicate="owes", object_entity_id=None,
        object_literal="$500", warrant_kind="stated",
        source_url=None, source_excerpt=None, valid_from=1, created_at=1,
        instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
    )

    out = render_facts(_view(
        stated,
        asserters={"0191f2c4-0000-7000-8000-000000000003": ("miles",)},
    ))

    assert "said by miles" in out
    # And NEVER as if it had a source. This rendered `(source: None — "")` for
    # every warrant but one, which is worse than either honest state: it reads
    # as a broken citation rather than as a claim somebody made.
    assert "source: None" not in out
    assert "None" not in out


def test_an_unattributed_stated_fact_says_so():
    """`nothing_found` on the attribution join is a real state — an unmapped
    sender, or a conversation whose messages were pruned. Rendering it as
    though nobody had to say it would make an unattributable claim look like a
    fact of the world."""
    stated = Fact(
        id="0191f2c4-0000-7000-8000-000000000004", workspace="nora",
        subject_id="x", predicate="y", object_entity_id=None,
        object_literal="z", warrant_kind="stated",
        source_url=None, source_excerpt=None, valid_from=1, created_at=1,
        instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
    )

    out = render_facts(_view(stated))

    assert "not recorded" in out
    assert "None" not in out


def test_no_warrant_kind_renders_as_a_missing_source():
    """All four, because the defect was that three of them shared one branch
    written for the fourth."""
    for kind, expected in (
        ("observation", "the room keeps saying"),
        ("premises", "worked out from"),
    ):
        f = Fact(
            id="0191f2c4-0000-7000-8000-000000000005", workspace="nora",
            subject_id="x", predicate="y", object_entity_id=None,
            object_literal="z", warrant_kind=kind,
            source_url=None, source_excerpt=None, valid_from=1, created_at=1,
            instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
        )
        out = render_facts(_view(f))
        assert expected in out, kind
        assert "None" not in out, kind


def test_the_extent_line_renders_even_when_no_slice_matched():
    """The whole point of it (23 §3). A block that vanishes when nothing
    matched tells her nothing exists; a block saying she holds forty and is
    being shown none tells her the verb is worth reaching for — which is the
    defect the recency window had, a subset presented as the whole."""
    out = render_facts(FactsView(extent=FactExtent(standing=40, shown=0)))

    assert "40 in all" in out
    assert "not shown here" in out


def test_the_extent_line_counts_and_never_lists():
    """"Counts only, never rows" — 19 §6. A slice that leaked a row into the
    extent would be a fourth slice nobody capped."""
    f = _fact()
    out = render_facts(_view(f, standing=40))

    line = next(ln for ln in out.splitlines() if ln.startswith("("))
    assert "black_tower" not in line
    assert "39 not shown" in line


def test_a_fact_in_both_slices_is_rendered_once():
    """The sender slice grounds «you told us…»; a line repeated under two
    headings reads as two facts."""
    f = _fact()
    out = render_facts(FactsView(
        room=(f,), sender=(f,), extent=FactExtent(standing=1, shown=1),
    ))

    assert out.count("black_tower") == 1
    # The positive control: the sender heading appears only when the slice has
    # something LEFT after deduplication, so its absence here is the dedupe and
    # not a heading that never renders.
    other = Fact(
        id="0191f2c4-0000-7000-8000-00000000000a", workspace="nora",
        subject_id="s", predicate="p", object_entity_id=None,
        object_literal="different", source_url="https://x.test/b",
        source_excerpt="x", valid_from=1, created_at=1,
        instruction_sha256=DIGEST, model=MODEL, established_by="tick-1",
    )
    both = render_facts(FactsView(
        room=(f,), sender=(other,), extent=FactExtent(standing=2, shown=2),
    ))
    assert "things they told her" in both


def _disputed(**over):
    kw = {
        "id": "0191f2c4-0000-7000-8000-00000000000d", "workspace": "nora",
        "subject_id": "big walk", "predicate": "means",
        "object_entity_id": None, "object_literal": "a walking activity",
        "source_url": "https://x.test/a", "source_excerpt": "x",
        "valid_from": 1, "created_at": 1, "instruction_sha256": DIGEST,
        "model": MODEL, "established_by": "tick-1", "disputed_at": 5,
        "disputed_by": "grant", "disputed_reason": "it's a video game",
    }
    kw.update(over)
    return Fact(**kw)


def test_a_disputed_claim_renders_as_a_dispute_and_does_not_vanish():
    """THE THREE-STATE RENDER — standing, disputed-and-marked, gone. 30 §2.1,
    29 §5 and 19 §4 all decide it and only step 4's shorthand collapsed it to
    two.

    Following the shorthand would have been the mute: one message from one
    member removing a true claim from every future turn, for everyone, with no
    way back."""
    out = render_facts(_view(_disputed()))

    assert "a walking activity" in out
    assert "DISPUTED by grant" in out
    assert "it's a video game" in out
    assert "not yet confirmed" in out


def test_a_disputed_claim_with_no_reason_still_says_who():
    """A deterministic mark carries no reason text, and an unmapped sender has
    no person. Neither may render as though the claim were unchallenged."""
    out = render_facts(_view(_disputed(disputed_reason=None, disputed_by=None)))

    assert "DISPUTED by someone" in out
    assert "None" not in out


def test_an_undisputed_claim_carries_no_dispute_text():
    """The positive control: a renderer that always appended the marker would
    satisfy both tests above while making every claim look contested."""
    out = render_facts(_view(_fact()))

    assert "DISPUTED" not in out
