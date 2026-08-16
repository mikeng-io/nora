"""Tests for `nora.language.render.render_room_language` — the capped,
evidence-carrying prefill block. This is the only thing the model ever sees
of the Dreamer's work; everything upstream exists to make it trustworthy.

The store (`SqlLanguageStore.room_language`, Task 7) already sorts by
`recognition_score DESC` and caps examples at 3, most-recent-first. This
renderer must NOT re-sort, re-rank, or re-filter by score — `RoomLexeme`
deliberately carries no score field so nothing downstream can reorder it.
"""

from __future__ import annotations

from nora.domain.models import LexemeExample, RoomLanguage, RoomLexeme
from nora.language.render import render_room_language


def example(text: str, *, message_id: int = 1, person_id: str | None = "p1") -> LexemeExample:
    return LexemeExample(message_id=message_id, person_id=person_id, text=text)


def lexeme(
    canonical_form: str,
    *,
    sense: str = "sense",
    occurrence_count: int = 1,
    distinct_speakers: int = 1,
    first_seen_ms: int = 0,
    last_seen_ms: int = 0,
    state: str = "live",
    examples: list[LexemeExample] | None = None,
) -> RoomLexeme:
    if examples is None:
        examples = [example(f"quote for {canonical_form}")]
    return RoomLexeme(
        canonical_form=canonical_form,
        sense=sense,
        occurrence_count=occurrence_count,
        distinct_speakers=distinct_speakers,
        first_seen_ms=first_seen_ms,
        last_seen_ms=last_seen_ms,
        state=state,
        examples=tuple(examples),
    )


def language_with(*lexemes: RoomLexeme) -> RoomLanguage:
    return RoomLanguage(scope_id="g1", lexemes=tuple(lexemes), generated_ms=0)


def test_an_empty_language_renders_no_block_at_all():
    """Not an empty block — NO block. A new room has no evidence, and a
    `<room_language>` tag with nothing in it invites the model to invent
    something to fill it."""
    assert render_room_language(RoomLanguage(scope_id="g1"), max_entries=10) == ""


def test_every_entry_carries_a_verbatim_example():
    """The gloss is model-authored; the quote is not. An entry with no quotable
    example must not render, because the gloss would then be an assertion with
    nothing under it."""
    block = render_room_language(language_with(
        lexeme("kam", sense="尷", examples=[example("個plan好撚kam")]),
        lexeme("ghost", sense="失蹤", examples=[]),
    ), max_entries=10)
    assert "個plan好撚kam" in block
    assert "ghost" not in block


def test_the_block_is_capped_by_score():
    """A room active for a year accumulates entries forever, and this block
    sits in the UNCACHED tier — so the cap is a cost control, not tidiness."""
    block = render_room_language(language_with(*[lexeme(f"w{i}") for i in range(50)]),
                                 max_entries=5)
    assert block.count("\n  ") == 5


def test_the_closing_frame_is_present_and_fixed():
    """Authored template text, never generated — it is what stops the list
    reading as a menu of words to reach for."""
    block = render_room_language(language_with(lexeme("kam")), max_entries=10)
    assert "not a set of words to reach for" in block
    assert "not a description of anybody" in block


def test_max_entries_zero_renders_no_block():
    """The boundary the cap-check-after-append bug missed: `max_entries=0`
    means zero entries, and a zero-entry block is no block at all — same as
    an empty `RoomLanguage`. Catches the regression where the loop appended
    the entry BEFORE checking the cap, so `max_entries=0` still rendered one
    entry (the cap could only ever be exceeded by one, never actually be
    zero)."""
    block = render_room_language(
        language_with(lexeme("a"), lexeme("b")), max_entries=0)
    assert block == ""


def test_max_entries_one_renders_exactly_one_entry():
    block = render_room_language(
        language_with(lexeme("a"), lexeme("b"), lexeme("c")), max_entries=1)
    assert block.count("\n  ") == 1
    assert "  a  " in block
    assert "  b  " not in block
    assert "  c  " not in block


def test_a_long_quote_is_truncated_with_an_ellipsis():
    """Finding 3 (final whole-branch review): `max_entries` caps how many
    LINES render, nothing capped how long ONE line's own fields could be —
    and `examples[].text` is now a real message body (Finding 1's fix), which
    is `sa.Text` with no length limit. A message body far past the 160-char
    quote cap must be cut down, with a visible marker that it was cut, not
    silently accepted whole."""
    long_body = "x" * 500
    block = render_room_language(language_with(
        lexeme("kam", sense="尷", examples=[example(long_body)]),
    ), max_entries=10)
    assert long_body not in block
    assert ("x" * 159 + "…") in block, "cut to 160 chars total, ellipsis included"


def test_canonical_form_and_sense_are_also_truncated():
    """The same unbounded-field argument applies to `canonical_form`
    (40 chars) and `sense` (80 chars) — both are model-authored `sa.Text`
    columns with no length limit of their own."""
    block = render_room_language(language_with(
        lexeme("c" * 200, sense="s" * 200, examples=[example("short quote")]),
    ), max_entries=10)
    assert "c" * 200 not in block
    assert "s" * 200 not in block
    assert ("c" * 39 + "…") in block
    assert ("s" * 79 + "…") in block


def test_a_short_quote_renders_unchanged():
    """The truncation must not add an ellipsis to text already under the
    cap — a boundary check against an off-by-one in `_truncate`."""
    block = render_room_language(language_with(
        lexeme("kam", sense="尷", examples=[example("好撚kam")]),
    ), max_entries=10)
    assert "好撚kam」" in block, "short enough to render whole, no ellipsis"


def test_entries_render_in_the_given_order_not_resorted():
    """Ruling 1 is load-bearing: `RoomLexeme` carries no score field so
    nothing downstream can re-rank, and what the model sees must be ranked by
    the store's evidence, not by anything this renderer decides. The three
    lexemes below are given in an order that does NOT match alphabetical
    order, occurrence_count order, distinct_speakers order, or first_seen_ms
    order — so a `sorted()` introduced on ANY visible field would move at
    least one of them and this test would go red. Must fail if a `sorted()`
    (by any key) is introduced into the render loop."""
    block = render_room_language(language_with(
        lexeme("zeta", occurrence_count=1, distinct_speakers=5, first_seen_ms=300_000),
        lexeme("alpha", occurrence_count=9, distinct_speakers=1, first_seen_ms=100_000),
        lexeme("mid", occurrence_count=5, distinct_speakers=3, first_seen_ms=200_000),
    ), max_entries=10)
    assert block.index("zeta") < block.index("alpha") < block.index("mid")
