"""Renders `RoomLanguage` into the one `<room_language>` block the model ever
sees of the Dreamer's work. Everything upstream — the observer, the scorer,
the store — exists to make this block trustworthy; this module turns the
dataclass into a string and does nothing else.

Three rulings, already made, implemented here rather than re-litigated:

1. **The store owns ordering.** `SqlLanguageStore.room_language` already
   returns only `live`/`fading` entries sorted by `recognition_score DESC`.
   `RoomLexeme` deliberately carries no score field so nothing downstream —
   including this module — can re-sort or re-rank. Entries render in the
   order given.
2. **An entry with no quotable example must not render.** The `sense` is
   model-authored; the example is not. The verbatim quote sitting under the
   gloss is the only thing that makes a wrong gloss visible — a bare gloss is
   an assertion with nothing under it.
3. **An empty `RoomLanguage` renders NO block at all** — not an empty
   `<room_language>` tag. A new room has no evidence, and an empty tag
   invites the model to invent something to fill it.

This block renders into the prefill's UNCACHED volatile tier (see
`nora/agent/prefill.py`'s cache-hit notes) — every token here is paid on
every turn, so `max_entries` is a cost control, not tidiness.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.models import RoomLanguage, RoomLexeme
from ..domain.prompt import escape

_CLOSING_FRAME = (
    "This describes how the room already uses these words — not a set of "
    "words to reach for, and not a description of anybody in it."
)


def render_room_language(language: RoomLanguage, *, max_entries: int) -> str:
    """`""` when there is nothing to say — either no lexemes at all, or none
    of them survive the "must carry a verbatim example" filter."""
    if not language:
        return ""

    lines: list[str] = []
    for lex in language.lexemes:
        if not lex.examples:
            continue
        lines.append(_render_entry(lex))
        if len(lines) >= max_entries:
            break

    if not lines:
        return ""

    body = "\n".join(lines)
    return (
        "<room_language>\n"
        "Words this room has made its own:\n"
        f"{body}\n"
        f"{_CLOSING_FRAME}\n"
        "</room_language>"
    )


def _render_entry(lexeme: RoomLexeme) -> str:
    quote = escape(lexeme.examples[0].text)
    since = _date(lexeme.first_seen_ms)
    return (
        f"  {escape(lexeme.canonical_form)}  {escape(lexeme.sense)}  「{quote}」  "
        f"{lexeme.occurrence_count}×, {lexeme.distinct_speakers} people, since {since}"  # noqa: RUF001 - the multiplication sign is the brief's own format
    )


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
