"""Unit tests for `nora.language.portrait_synthesizer.PortraitSynthesizer` —
template construction over both/either/neither source present. The identity-
stripping guarantee (`_LexemeFact`/`_ExchangeFact` never carry `person_id`/
`participants`) is covered behaviourally, against the real synthesizer, by
`tests/test_acceptance_rules.py::test_room_portrait_narrative_cannot_reference_a_person`
(DEC-121) — not duplicated here.
"""

from __future__ import annotations

import pytest

from nora.domain.models import (
    ExchangeExample,
    LexemeExample,
    RoomExchange,
    RoomExchangePattern,
    RoomLanguage,
    RoomLexeme,
)
from nora.language.portrait_synthesizer import PortraitSynthesizer

pytestmark = pytest.mark.asyncio


def _lexeme(canonical_form: str, occurrence_count: int = 1) -> RoomLexeme:
    return RoomLexeme(
        canonical_form=canonical_form, sense="a group thing", occurrence_count=occurrence_count,
        distinct_speakers=1, first_seen_ms=0, last_seen_ms=0, state="live",
        examples=(LexemeExample(message_id=1, person_id="p1", text="quote"),),
    )


def _exchange_pattern(act: str, occurrence_count: int = 1) -> RoomExchangePattern:
    return RoomExchangePattern(
        act=act, participants=("p1", "p2"), occurrence_count=occurrence_count,
        first_seen_ms=0, last_seen_ms=0, state="live",
        examples=(ExchangeExample(message_id=1, person_id="p1", text="quote"),),
    )


class _Reader:
    def __init__(self, language: RoomLanguage, exchange: RoomExchange) -> None:
        self._language = language
        self._exchange = exchange

    async def room_language(self, *, scope_id: str, limit: int) -> RoomLanguage:
        return self._language

    async def room_exchange(self, *, scope_id: str, limit: int) -> RoomExchange:
        return self._exchange


class _Store:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upsert_portrait(
        self, *, scope_id: str, narrative: str,
        lexeme_basis: tuple[str, ...], exchange_basis: tuple[str, ...],
    ) -> None:
        self.calls.append({
            "scope_id": scope_id, "narrative": narrative,
            "lexeme_basis": lexeme_basis, "exchange_basis": exchange_basis,
        })


def _synth(language: RoomLanguage, exchange: RoomExchange, store: _Store) -> PortraitSynthesizer:
    reader = _Reader(language, exchange)
    return PortraitSynthesizer(
        language=reader, exchange=reader, store=store,
        max_rendered_lexemes=6, max_rendered_exchanges=4,
    )


async def test_both_sources_empty_upserts_an_empty_narrative():
    store = _Store()
    await _synth(
        RoomLanguage(scope_id="g1"), RoomExchange(scope_id="g1"), store,
    ).synthesize(scope_id="g1")
    assert store.calls == [{
        "scope_id": "g1", "narrative": "", "lexeme_basis": (), "exchange_basis": (),
    }]


async def test_lexemes_only_mentions_vocabulary_not_exchange_patterns():
    store = _Store()
    language = RoomLanguage(scope_id="g1", lexemes=(_lexeme("big walk"),))
    await _synth(language, RoomExchange(scope_id="g1"), store).synthesize(scope_id="g1")
    narrative = store.calls[0]["narrative"]
    assert "big walk" in narrative
    assert "Its people" not in narrative


async def test_exchanges_only_mentions_patterns_not_vocabulary():
    store = _Store()
    exchange = RoomExchange(scope_id="g1", patterns=(_exchange_pattern("banter"),))
    await _synth(RoomLanguage(scope_id="g1"), exchange, store).synthesize(scope_id="g1")
    narrative = store.calls[0]["narrative"]
    assert "Its people" in narrative
    assert "banter — light jabs" in narrative
    assert "own vocabulary" not in narrative


async def test_both_sources_present_mentions_both_and_closes_with_the_disclaimer():
    store = _Store()
    language = RoomLanguage(scope_id="g1", lexemes=(_lexeme("big walk"),))
    exchange = RoomExchange(scope_id="g1", patterns=(_exchange_pattern("banter"),))
    await _synth(language, exchange, store).synthesize(scope_id="g1")
    narrative = store.calls[0]["narrative"]
    assert "big walk" in narrative
    assert "Its people" in narrative
    assert "not a claim about any one person" in narrative


async def test_narrative_mentions_at_most_three_lexemes():
    store = _Store()
    language = RoomLanguage(scope_id="g1", lexemes=tuple(
        _lexeme(f"word{i}") for i in range(6)
    ))
    await _synth(language, RoomExchange(scope_id="g1"), store).synthesize(scope_id="g1")
    narrative = store.calls[0]["narrative"]
    assert sum(f"word{i}" in narrative for i in range(6)) == 3


async def test_lexeme_basis_and_exchange_basis_record_what_was_used():
    store = _Store()
    language = RoomLanguage(scope_id="g1", lexemes=(_lexeme("big walk"),))
    exchange = RoomExchange(scope_id="g1", patterns=(_exchange_pattern("banter"),))
    await _synth(language, exchange, store).synthesize(scope_id="g1")
    call = store.calls[0]
    assert call["lexeme_basis"] == ("big walk",)
    assert call["exchange_basis"] == ("banter",)


async def test_a_second_synthesis_replaces_rather_than_appends():
    """`upsert_portrait` is called fresh each time with the CURRENT state —
    the synthesizer itself does no accumulation; that guarantee lives in the
    store's UPSERT (proved against a real Postgres in
    `tests/infra/test_room_portrait_store.py`), but this confirms the
    synthesizer always hands the store a complete, self-contained call."""
    store = _Store()
    language = RoomLanguage(scope_id="g1", lexemes=(_lexeme("big walk"),))
    synthesizer = _synth(language, RoomExchange(scope_id="g1"), store)
    await synthesizer.synthesize(scope_id="g1")
    await synthesizer.synthesize(scope_id="g1")
    assert len(store.calls) == 2
    assert store.calls[0] == store.calls[1]
