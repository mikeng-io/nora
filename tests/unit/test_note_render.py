from __future__ import annotations

from nora.domain.block_types import BLOCK_TYPES
from nora.domain.notes import DESCRIPTION_MAX_CHARS, TITLE_MAX_CHARS, Note
from nora.domain.prompt import escape_attr
from nora.language.note_render import render_notes

#: What `<notes>` says it is, declared once on the type (#314). It used to be
#: a `NOTES_BLOCK_NOTE` constant here AND a `NOTES_HEADER` catalogue line in
#: `prefill` — two strings for one fact, free to disagree, which four block
#: types managed to do before the registry existed.
NOTES_BLOCK_NOTE = BLOCK_TYPES["notes"].description


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
        id=note_id, unit="nora", title=title, description=description,
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
    is FALSE for a unit-keyed block — a note may have been established in
    a different room of the same unit (`Note.established_in` is a routing
    key, never a filter). It would be false in the one place the model is being
    asked to trust a sentence."""
    assert "this room's own messages" not in NOTES_BLOCK_NOTE
    assert "instruction to you" in NOTES_BLOCK_NOTE


def test_the_note_says_whose_words_the_index_is_in():
    """Issue #226. The block said what the lines were ABOUT and never whose
    words they were, so a description her own curator wrote for triage — capped
    at 200 characters, hedged — was read back to the room it was about as a
    flat claim about that room. The three properties the sentence now has to
    carry, asserted separately so dropping any one of them reddens on its own:

      - authorship — hers, for herself;
      - not the room's — neither something said here nor a record of what was;
      - where a claim about the room comes from instead — the room's own
        messages.

    That last clause used to name «the transcript», and it stopped being true
    of every reader when this block became one type across four passes (#314):
    the proactive decide turn renders no message block at all, so it was told
    to prefer a thing its prompt does not contain. It now says «where you can
    ALSO see the room's own messages» — vacuous where there are none, true
    where there are, and never pointing at an absent block.
    """
    note = NOTES_BLOCK_NOTE.lower()
    assert "your own notes" in note and "written by you for yourself" in note
    assert "not anything this room said" in note
    assert "not a record of what happened" in note
    assert "from what the room actually did" in note
    # And the tie-break said outright, because a summary is the thing that
    # reads as settled: on `a10` the stale line is what she reached for.
    assert "an entry is what you wrote, not what happened" in note
    assert "where you can also see the room's own messages and they "\
           "disagree with an entry, the messages are what happened" in note


def test_the_note_forbids_reading_a_line_out_as_well_as_acting_on_one():
    """The pointer clause was a read-before-WRITE latch: it stopped her
    revising a body she had not read and said nothing about SPEAKING, which is
    the path the message actually took. Both halves are pinned — the latch
    (`read one before acting`) and the new one (`never read a line out`) —
    because an author tightening one is exactly how the other goes."""
    note = NOTES_BLOCK_NOTE.lower()
    assert "read one before acting on what it holds" in note
    assert "never read an entry out as if it were what was said" in note


def test_the_note_keeps_the_machinery_out_of_the_room():
    """The second half of the same incident: asked point-blank what she had
    changed, she answered by explaining the mechanism — that the room's 「open
    note」 was still unsettled and the deadline near. 「open note」 is the tool
    layer's phrase, never the room's. The block she reads on every turn is
    where the tier says its own vocabulary stays inside it.

    Scoped to the APPARATUS, and both halves are asserted, because the ban on
    its own is wider than the defect. #226(b) was the tool layer's WORD, not
    the subject; an unqualified «never mention the notes» would forbid the
    honest answer `nora/doctrine/conduct.md` already requires when she has
    looked and found nothing, and would contradict, on the same turn, a skill
    that tells her she holds what a room has left unsettled. The permission
    sentence is what keeps the ban readable as a ban on the machinery."""
    assert "never name this bookkeeping itself" in NOTES_BLOCK_NOTE
    assert "this list, the tools that write it, the word for them" in NOTES_BLOCK_NOTE
    # And the scope, in the same breath: the subject is hers to raise.
    assert ("what one of them is about, and what you are still holding open, "
            "are yours to say plainly") in NOTES_BLOCK_NOTE.lower()


def test_the_note_still_invites_her_to_raise_what_a_note_is_about():
    """Why the permission is here, said at the strength the instrument
    supports and no higher.

    A first wording of this work said only what she may NOT do with a note —
    never read one out, never name the apparatus — and nothing about raising
    the subject, which is the entire purpose of the tier. Stacking those
    prohibitions across this block, the fire header and the tool schemas read
    2/15 and 3/15 on the act suite's `a01`, the case that measures whether she
    still speaks at all.

    **That is not a demonstrated regression, and no number here is a
    prediction.** The same UNCHANGED prompt read 5/15, 8/15, 10/15 and 12/15
    across four windows of one session, so a 2-3/15 reading sits inside a band
    the instrument cannot separate from noise, and nothing attributes it to
    this clause rather than the other two. Issue #158 is the general form: this
    suite's n does not support a per-clause causal claim, and a docstring that
    made one would be telling the next author a fact nothing measured.

    So the clause stays on an argument, not a number: it costs one sentence,
    and what it guards against — three prohibitions reading as «leave the notes
    alone» — is the tier's whole point going quiet. A later tightening that
    drops it is making the same unmeasured bet in the other direction, and
    should re-run the suite rather than trust either figure above."""
    note = NOTES_BLOCK_NOTE.lower()
    assert "raise what one of them is about whenever it would help" in note


def test_the_opening_tag_carries_what_the_block_is():
    """Same ruling as every sibling block: an attribute is the one position
    rendered content can never reach, and it is read before the body. A
    regression that moved this into the body text — still true, but no longer
    load-bearing at the un-spoofable position — would pass every other test in
    this file.

    There is no `untrusted="true"` beside it any more and its absence is the
    point (#314): that token's definition lived in one doctrine file which this
    audience does not load, so the marker reached the model as a word with no
    meaning attached. The clauses above are the meaning, and they are here.
    """
    out = _text(render_notes((_note(),)))
    opening_tag = out[: out.index(">") + 1]
    assert escape_attr(NOTES_BLOCK_NOTE) in opening_tag
    assert "untrusted" not in opening_tag


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
    assert out.count("\n<note ") == 1


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
    assert out.count("\n<note ") == 60


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
