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
