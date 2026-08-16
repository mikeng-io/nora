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

**Per-field character caps.** `max_entries` bounds the number of rendered
lines; nothing bounded a single line's own length until now. `canonical_form`/
`sense` are model-authored and `examples[].text` is now a real message body
(see `nora.infra.sql.language`'s module docstring) — all three are `sa.Text`
columns with no length limit, so 6 capped ENTRIES times an unbounded FIELD is
still unbounded per-turn cost on the uncached tier, and it persists for as
long as the lexeme stays `live`/`fading` (21+ days). It is also a persistence
channel: a crafted group message can put attacker-authored prose into every
subsequent turn's prefill, in a semi-authoritative position under the
`<room_language>` tag — `escape()` (`nora/domain/prompt.py`) blocks tag
forgery, not instruction text. `_render_entry` below truncates each of the
three fields at render time (40/80/160 chars); `nora.language.observer
._parse_item` additionally REJECTS a candidate whose `canonical_form`/`sense`
is implausibly long before it ever reaches the store, so this is defence in
depth rather than the only guard.

**Store/renderer limit coupling.** `SqlLanguageStore.room_language` applies a
plain `ORDER BY recognition_score DESC LIMIT :limit` with no awareness of
whether an entry carries a quotable example — that filter is rule 2, and it
lives here, not in the store. So if a caller asks the store for exactly
`max_entries` rows and some of those rows turn out to lack an example, this
renderer drops them (correctly — rule 2 wins) and CANNOT reach further down
to replace them, because it was only ever given `max_entries` rows to begin
with. The result is a block with fewer than `max_entries` lines even though
the room has enough evidence to fill it. To reliably fill the cap, callers
must request MORE rows from the store than `max_entries` actually rendered
here — the two limits are related but must not be set equal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.models import RoomLanguage, RoomLexeme
from ..domain.prompt import escape

_CLOSING_FRAME = (
    "This describes how the room already uses these words — not a set of "
    "words to reach for, and not a description of anybody in it."
)

# Per-field render caps — see the module docstring's "Per-field character
# caps" note for why these exist. Suggested values from the finding that
# named this gap; not derived from anything else in the file.
_CANONICAL_FORM_MAX_CHARS = 40
_SENSE_MAX_CHARS = 80
_QUOTE_MAX_CHARS = 160


def render_room_language(language: RoomLanguage, *, max_entries: int) -> str:
    """`""` when there is nothing to say — either no lexemes at all, or none
    of them survive the "must carry a verbatim example" filter."""
    if not language:
        return ""

    lines: list[str] = []
    for lex in language.lexemes:
        if len(lines) >= max_entries:
            break
        if not lex.examples:
            continue
        lines.append(_render_entry(lex))

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
    canonical_form = escape(_truncate(lexeme.canonical_form, _CANONICAL_FORM_MAX_CHARS))
    sense = escape(_truncate(lexeme.sense, _SENSE_MAX_CHARS))
    quote = escape(_truncate(lexeme.examples[0].text, _QUOTE_MAX_CHARS))
    since = _date(lexeme.first_seen_ms)
    return (
        f"  {canonical_form}  {sense}  「{quote}」  "
        f"{lexeme.occurrence_count}×, {lexeme.distinct_speakers} people, since {since}"  # noqa: RUF001 - the multiplication sign is the brief's own format
    )


def _truncate(text: str, max_chars: int) -> str:
    """`text` cut to at most `max_chars`, with a trailing ellipsis marking the
    cut — applied BEFORE `escape()`, not after: truncating post-escape could
    slice a multi-character escaped sequence in half. `max_chars` counts
    Python characters, not bytes or grapheme clusters — good enough for a
    display cap, and consistent with `str`'s own `len()` everywhere else in
    this module (`_date`, the occurrence line)."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
