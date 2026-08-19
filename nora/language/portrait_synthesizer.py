"""Dreamer Plan 3 (2026-08-20 room-portrait design) — synthesizes `room_lexeme`
and `room_exchange` into one narrative, `RoomPortrait`. No model call anywhere
in this module: Plan 1/2's own module docstrings argue for keeping "an LLM
glosses, arithmetic/template decides adoption" as separate concerns, and both
inputs here are already model-vetted, already-structurally-safe output — a
THIRD model call synthesizing prose from them would reintroduce exactly the
propositional-content risk DEC-111 exists to close off, for no benefit.

**The structural guarantee against person-level attribution.** `_LexemeFact`/
`_ExchangeFact` below carry no `person_id`/`participants` field — a person's
identity is not merely unread by `_build_narrative`, it is not a field that
exists on the type that function can see. This is the same "the invariant
lives where a caller cannot forget it" move DEC-111/DEC-120 already make one
layer down (`is_nora = 0` living inside `RecordStore.since`; the DB `CHECK` on
`room_exchange.participants`) — here expressed as a type boundary instead of a
database constraint, because a portrait's `narrative` column is free text and
a `CHECK` cannot meaningfully forbid "contains a person's id" for an arbitrary
string. `PortraitSynthesizer.synthesize` reads via the SAME `RoomLanguageReader
`/`RoomExchangeReader` ports and the SAME `max_rendered_lexemes`/
`max_rendered_exchanges` limits `TranscriptInjection` already uses for
`<room_language>`/`<room_exchange>` — a portrait narrates only what she can
already see elsewhere in her own context, never more.

`nora/language/` may not import `strands`, `nora.agent.factory`, or
`nora.config` (see `nora/language/observer.py`'s own module docstring for the
boundary this is held to) — `PortraitSynthesizer` takes its reader/writer
collaborators through `__init__` only, no config read here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from nora.domain.models import RoomExchange, RoomLanguage
from nora.domain.ports import RoomExchangeReader, RoomLanguageReader

from .exchange_render import ACT_GLOSSES

_CLOSING_FRAME = (
    "This is a synthesis of the two blocks above — not a claim about any "
    "one person."
)

# How many facts the template will ever mention in one sentence — a cost
# control on the uncached prefill tier, same reasoning `render.py`'s
# `max_entries` already states. Deliberately small and NOT configurable (see
# the design doc's "Config" section): the inputs are already capped by
# `max_rendered_lexemes`/`max_rendered_exchanges`, so this is a second,
# tighter cap on how many of THOSE feed one sentence, not an independent
# knob.
_MAX_LEXEMES_IN_NARRATIVE = 3
_MAX_ACTS_IN_NARRATIVE = 2


@dataclass(frozen=True)
class _LexemeFact:
    """A `RoomLexeme` stripped to exactly what the narrative may use. NO
    `person_id`, NO `examples` — `LexemeExample.person_id` never crosses this
    boundary."""

    canonical_form: str
    occurrence_count: int


@dataclass(frozen=True)
class _ExchangeFact:
    """A `RoomExchangePattern` stripped to exactly what the narrative may
    use. NO `participants` — `RoomExchangePattern.participants` never
    crosses this boundary."""

    act: str
    occurrence_count: int


def _project_lexemes(language: RoomLanguage) -> tuple[_LexemeFact, ...]:
    return tuple(
        _LexemeFact(canonical_form=lex.canonical_form, occurrence_count=lex.occurrence_count)
        for lex in language.lexemes
    )


def _project_exchanges(exchange: RoomExchange) -> tuple[_ExchangeFact, ...]:
    return tuple(
        _ExchangeFact(act=pattern.act, occurrence_count=pattern.occurrence_count)
        for pattern in exchange.patterns
    )


def _build_narrative(
    lexemes: Sequence[_LexemeFact], exchanges: Sequence[_ExchangeFact],
) -> str:
    """Deterministic template — see module docstring for why this is not a
    model call. Returns `""` when there is nothing to say (mirrors
    `render_room_language`/`render_room_exchange`'s own "empty renders
    nothing" ruling, one layer up: an empty narrative here means the
    prefill's `_room_portrait_block` emits no `<room_portrait>` tag at all)."""
    sentences: list[str] = []
    if lexemes:
        forms = [lex.canonical_form for lex in lexemes[:_MAX_LEXEMES_IN_NARRATIVE]]
        vocab = _join(forms)
        sentences.append(f"This room has its own vocabulary — {vocab} among it.")
    if exchanges:
        glosses = [
            ACT_GLOSSES.get(fact.act, fact.act) for fact in exchanges[:_MAX_ACTS_IN_NARRATIVE]
        ]
        sentences.append(f"Its people {_join(glosses)}.")
    if not sentences:
        return ""
    sentences.append(_CLOSING_FRAME)
    return " ".join(sentences)


def _join(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", and {items[-1]}"


class RoomPortraitWriter(Protocol):
    """The write side `PortraitSynthesizer` needs — UPSERT, not append (a
    portrait has no evidence layer, see module docstring)."""

    async def upsert_portrait(
        self, *, scope_id: str, narrative: str,
        lexeme_basis: tuple[str, ...], exchange_basis: tuple[str, ...],
    ) -> None: ...


class PortraitSynthesizer:
    """`RoomPortraitSynthesizer` (see `nora.domain.ports`). Reads Plan 1/2's
    current top-N state, projects it through `_project_lexemes`/
    `_project_exchanges` (stripping identity at the type boundary), and
    UPSERTs the resulting narrative. Never raises out of `synthesize` on an
    empty result — an empty narrative is a real, correct state (mirrors
    `RoomLanguage`/`RoomExchange`'s own "empty is real" ruling); it still
    UPSERTs an empty narrative so a portrait that used to have material and
    no longer does clears rather than serving a stale one."""

    def __init__(
        self, *, language: RoomLanguageReader, exchange: RoomExchangeReader,
        store: RoomPortraitWriter, max_rendered_lexemes: int, max_rendered_exchanges: int,
    ) -> None:
        self._language = language
        self._exchange = exchange
        self._store = store
        self._max_rendered_lexemes = max_rendered_lexemes
        self._max_rendered_exchanges = max_rendered_exchanges

    async def synthesize(self, *, scope_id: str) -> None:
        language = await self._language.room_language(
            scope_id=scope_id, limit=self._max_rendered_lexemes)
        exchange = await self._exchange.room_exchange(
            scope_id=scope_id, limit=self._max_rendered_exchanges)
        lexemes = _project_lexemes(language)
        exchanges = _project_exchanges(exchange)
        narrative = _build_narrative(lexemes, exchanges)
        await self._store.upsert_portrait(
            scope_id=scope_id, narrative=narrative,
            lexeme_basis=tuple(fact.canonical_form for fact in lexemes),
            exchange_basis=tuple(fact.act for fact in exchanges),
        )
