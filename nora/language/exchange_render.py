"""Renders `RoomExchange` into the `<room_exchange>` block — the exchange
sibling of `nora.language.render`. Same three rulings, plus a fourth this
feature adds:

1. The store owns ordering (`SqlExchangeStore.room_exchange` sorts by
   `recognition_score DESC`; `RoomExchangePattern` carries no score field).
2. An entry with no quotable example must not render.
3. An empty `RoomExchange` renders NO block at all.
4. **The closing frame states the withhold rule in plain language** — on top
   of, not instead of, the structural guarantees in
   `nora.language.exchange_observer`/`nora.infra.sql.schema.EXCHANGE_ACTS`
   (a closed-set `act` with no free-text gloss, and >= 2 participants
   enforced by a DB CHECK). Defence in depth, the same shape
   `nora.language.render`'s own per-field caps already are for Plan 1.

The rendered gloss for each `act` is REPO text (`ACT_GLOSSES` below), never
model text — there is nothing model-authored on this path except the closed
`act` selection itself and the verbatim quote. `ACT_GLOSSES` is public
(rather than `_`-prefixed) because Dreamer Plan 3
(`nora.language.portrait_synthesizer`) imports it too — the SAME repo text,
not a duplicated copy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.models import RoomExchange, RoomExchangePattern
from ..domain.prompt import escape
from .render import ROOM_BLOCK_NOTE

_CLOSING_FRAME = (
    "This describes patterns of interaction between these people — not a "
    "claim about any one of them, and not something to reach for."
)

ACT_GLOSSES = {
    "insult_trade": "trade insults, back and forth",
    "banter": "banter — light jabs, not meant seriously",
    "tease": "tease each other",
    "callback_joke": "run a callback joke between them",
    "agreement_ritual": "back each other up, reliably",
    "disagreement_ritual": "argue a point out, repeatedly",
    "compliment_exchange": "trade compliments",
}

_QUOTE_MAX_CHARS = 160


def render_room_exchange(exchange: RoomExchange, *, max_entries: int) -> str:
    """`""` when there is nothing to say — mirrors `render_room_language`."""
    if not exchange:
        return ""

    lines: list[str] = []
    for pattern in exchange.patterns:
        if len(lines) >= max_entries:
            break
        if not pattern.examples or not pattern.examples[0].text.strip():
            continue
        lines.append(_render_entry(pattern))

    if not lines:
        return ""

    body = "\n".join(lines)
    return (
        f'<room_exchange untrusted="true" note="{ROOM_BLOCK_NOTE}">\n'
        "Patterns this room's people fall into:\n"
        f"{body}\n"
        f"{_CLOSING_FRAME}\n"
        "</room_exchange>"
    )


def _render_entry(pattern: RoomExchangePattern) -> str:
    gloss = escape(ACT_GLOSSES.get(pattern.act, pattern.act))
    who = escape(f"{len(pattern.participants)} people")
    quote = escape(_truncate(pattern.examples[0].text, _QUOTE_MAX_CHARS))
    since = _date(pattern.first_seen_ms)
    return (
        f"  {gloss}  ({who})  「{quote}」  "
        f"{pattern.occurrence_count}×, since {since}"  # noqa: RUF001
    )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
