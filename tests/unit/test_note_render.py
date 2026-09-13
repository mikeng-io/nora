from __future__ import annotations

from nora.domain.notes import DESCRIPTION_MAX_CHARS, TITLE_MAX_CHARS, Note
from nora.language.note_render import NOTES_BLOCK_NOTE, render_notes


def _text(block) -> str:
    """The block's bytes, or `""` when there is no block.

    The three renderers return a `ContextBlock` since #220's second half — the
    block declares itself and `ContextBlock.render()` is the only thing that
    turns one into text. These tests assert on the bytes, so they render here.
    A `None` is «no block at all», which is what `""` used to mean.
    """
    return block.render() if block is not None else ""

def _note(
    note_id="0191f2c4-0000-7000-8000-000000000001",
    title="9 Sept dinner",
    description="Three venues on the table; nobody has picked one.",
    uncertainty="which venue, and whether the 9th still works for everyone",
    closing_condition="a venue is booked, or the date is abandoned",
    **kwargs,
) -> Note:
    return Note(
        id=note_id, workspace="nora", title=title, description=description,
        uncertainty=uncertainty, closing_condition=closing_condition,
        established_in="g1", created_at=1, head_revision_id=f"{note_id}-r1",
        anchor_date=20260909, **kwargs,
    )


def test_an_empty_tier_renders_no_block_at_all():
    """Its sibling's rule (`render_facts`, and `RoomLanguage.__bool__` before
    it): empty is a REAL state, and the renderer emits nothing rather than an
    empty block. This is what lets the tier ship dormant — `TagContext.notes`
    defaults to `()` and nothing writes it yet, so today this renders nothing
    in production and adds no tokens to any tag."""
    assert _text(render_notes(())) == ""


def test_the_index_carries_the_title_and_the_description():
    out = _text(render_notes((_note(),)))
    assert "9 Sept dinner" in out
    assert "Three venues on the table; nobody has picked one." in out


def test_the_body_is_not_in_the_index():
    """§5.1/§5.2: the index buys AWARENESS, `note_read` buys the content. The
    only free-text a `Note` carries beyond title/description is `uncertainty`
    and `closing_condition`, and neither renders — see `render_notes`'s
    docstring for the closing-condition ruling in particular. This is the test
    that turns red if somebody widens the line "while they are in here", which
    is how a per-tag block with no top-N grows without anyone deciding to."""
    out = _text(render_notes((_note(
        uncertainty="UNCERTAINTY-SENTINEL", closing_condition="CLOSING-SENTINEL",
    ),)))
    assert "UNCERTAINTY-SENTINEL" not in out
    assert "CLOSING-SENTINEL" not in out


def test_the_note_does_not_claim_the_room_produced_this():
    """`ROOM_BLOCK_NOTE` says «distilled from this room's own messages», which
    is FALSE for a workspace-keyed block — a note may have been established in
    a different room of the same workspace (`Note.established_in` is a routing
    key, never a filter). It would be false in the one place the model is being
    asked to trust a sentence."""
    assert "this room's own messages" not in NOTES_BLOCK_NOTE
    assert "instruction to you" in NOTES_BLOCK_NOTE


def test_the_opening_tag_carries_the_note_and_the_marker():
    """Same ruling as every sibling block: an attribute is the one position
    rendered content can never reach, and it is read before the body. A
    regression that moved the note into the body text — still true, but no
    longer load-bearing at the un-spoofable position — would pass every other
    test in this file."""
    out = _text(render_notes((_note(),)))
    opening_tag = out[: out.index(">") + 1]
    assert NOTES_BLOCK_NOTE in opening_tag
    assert 'untrusted="true"' in opening_tag


def test_everything_model_authored_is_escaped():
    """R-13. A title and a description are model-authored from group-authored
    material, so they reach the prompt through `nora.domain.prompt.escape` like
    every other injected block's contents."""
    out = _text(render_notes((_note(title="<injected>", description="a & b"),)))
    assert "<injected>" not in out
    assert "&lt;injected&gt;" in out
    assert "&amp;" in out


def test_a_retired_note_is_never_rendered_beside_a_standing_one():
    """A single-note input can pass a retirement test by coincidence (an
    empty-tuple bug and an unfiltered-list bug each satisfy it differently); a
    MIX forces the implementation to filter per-note rather than short-circuit
    on the whole sequence. `Note.is_standing` reads `retired_at` alone — unlike
    `Fact`, a note may retire with no successor."""
    standing = _note(title="standing note")
    retired = _note(
        note_id="0191f2c4-0000-7000-8000-000000000002",
        title="retired note", retired_at=99,
    )
    out = _text(render_notes((retired, standing)))
    assert "retired note" not in out
    assert "standing note" in out
    assert out.count("\n- ") == 1


def test_the_index_is_uncapped_and_every_standing_note_appears():
    """**The decision most likely to be quietly "optimised" away**, so it gets
    the test that turns red when it is.

    §5.1: no top-N, no elision, no relevance scoring. A note missing from the
    index is invisible to her, and invisible is indistinguishable from absent —
    the `unavailable`-reported-as-`nothing_found` failure R-27 exists to
    prevent, and a relevance-scored subset has the same defect with extra
    machinery. The prompt-budget answer is the PER-ENTRY cap in
    `nora.domain.notes` plus the curator's retirement pass, never a cap here.

    Sixty is well past any plausible top-N somebody would reach for, and the
    line count is asserted as well as the contents so a renderer that emitted
    an "and 40 more" elision line would still fail.
    """
    notes = tuple(
        _note(note_id=f"0191f2c4-0000-7000-8000-{i:012d}", title=f"note {i:03d}")
        for i in range(60)
    )
    out = _text(render_notes(notes))
    for i in range(60):
        assert f"note {i:03d}" in out, i
    assert out.count("\n- ") == 60


def test_the_entries_are_capped_upstream_not_here():
    """The other half of §5.1's bargain: uncapped index, capped entries. The
    caps are `Note.__post_init__`'s, so an over-long entry is unrepresentable
    rather than truncated at render — truncation at render is silent loss, and
    a renderer that quietly shortened would make this renderer the place the
    cap lives, which is exactly what `nora.domain.notes` moved away from.
    A maximal note renders in full."""
    out = _text(render_notes((_note(
        title="t" * TITLE_MAX_CHARS, description="d" * DESCRIPTION_MAX_CHARS,
    ),)))
    assert "t" * TITLE_MAX_CHARS in out
    assert "d" * DESCRIPTION_MAX_CHARS in out
