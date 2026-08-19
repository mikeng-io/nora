"""Tests for `nora.language.exchange_observer.ExchangePatternObserver` —
Dreamer Plan 2's one LLM call. Mirrors
`tests/unit/test_language_observer.py`'s structure and its fake-test
discipline (CLAUDE.md): `FakeModel` reproduces wrong-call shapes, not just a
clean one.
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.exchange_observer import ExchangePatternObserver
from nora.obs.metrics import Metrics

pytestmark = pytest.mark.asyncio


class FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, prompt: str) -> str:
        return self._responses.pop(0)


def rows(*specs: tuple[int, str, str]) -> list[TranscriptRow]:
    """`specs` is `(message_id, person_id, body)` — exchange patterns need
    control over WHO said each row, unlike lexeme tests' single-speaker
    default batches."""
    return [
        TranscriptRow(
            id=mid, sender_id=person_id, sender_name=person_id, ts=1_000 + mid,
            body=body, is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None, person_id=person_id,
        )
        for mid, person_id, body in specs
    ]


def _pattern(act: str, message_ids: list[int], confidence: float = 0.9) -> str:
    return json.dumps({"patterns": [
        {"act": act, "message_ids": message_ids, "confidence": confidence},
    ]})


async def test_a_batch_with_no_exchange_yields_no_candidates():
    observer = ExchangePatternObserver(FakeModel(responses=['{"patterns": []}']))
    result = await observer.observe(batch=rows((1, "miles", "食咩好")))
    assert result.candidates == ()
    assert result.parsed is True


async def test_malformed_json_yields_nothing_rather_than_raising():
    observer = ExchangePatternObserver(FakeModel(responses=["not json at all"]))
    result = await observer.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert result.candidates == ()
    assert result.parsed is False


async def test_an_act_outside_the_closed_set_is_dropped():
    """DEC-120's first structural guarantee: an `act` the model invents,
    rather than selecting from `EXCHANGE_ACTS`, must never reach a
    candidate — this is what makes a free-text disposition claim
    unconstructible."""
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("miles_is_dumb", [1, 2])]))
    result = await observer.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert result.candidates == ()
    assert result.parsed is True, "the response parsed; one item was rejected"


async def test_a_pattern_with_fewer_than_two_message_ids_is_dropped():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("banter", [1])]))
    result = await observer.observe(batch=rows((1, "miles", "x")))
    assert result.candidates == ()


async def test_a_pattern_whose_two_message_ids_are_the_same_speaker_is_dropped():
    """DEC-120's second structural guarantee, exercised at the join step:
    two message ids naming only ONE distinct person describe no exchange at
    all — a single participant is exactly the "disposition about a person"
    shape the withhold rule forbids."""
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("banter", [1, 2])]))
    result = await observer.observe(
        batch=rows((1, "miles", "haha"), (2, "miles", "lol")))
    assert result.candidates == ()


async def test_a_genuine_two_person_pattern_carries_both_participants():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("insult_trade", [1, 2])]))
    result = await observer.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))
    assert result.parsed is True
    [c] = result.candidates
    assert c.act == "insult_trade"
    assert set(c.participants) == {"miles", "tyler"}


async def test_a_pattern_naming_a_message_not_in_the_batch_is_dropped():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("banter", [1, 999])]))
    result = await observer.observe(batch=rows((1, "miles", "x")))
    assert result.candidates == ()
    assert result.parsed is True


async def test_an_empty_batch_makes_no_model_call():
    observer = ExchangePatternObserver(FakeModel(responses=[]))
    result = await observer.observe(batch=[])
    assert result.candidates == ()
    assert result.parsed is True


async def test_a_parse_failure_is_counted_distinctly_from_an_empty_extraction():
    metrics = Metrics()
    prose = ExchangePatternObserver(FakeModel(responses=["prose"]), metrics=metrics)
    await prose.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert metrics.get("nora_exchange_parse_failures_total") == 1

    empty = ExchangePatternObserver(
        FakeModel(responses=['{"patterns": []}']), metrics=metrics)
    await empty.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert metrics.get("nora_exchange_parse_failures_total") == 1


async def test_a_single_participant_drop_is_counted_distinctly():
    metrics = Metrics()
    observer = ExchangePatternObserver(
        FakeModel(responses=[_pattern("banter", [1, 2])]), metrics=metrics)
    await observer.observe(batch=rows((1, "miles", "a"), (2, "miles", "b")))
    assert metrics.get(
        "nora_exchange_candidates_dropped_total", reason="single_participant",
    ) == 1
