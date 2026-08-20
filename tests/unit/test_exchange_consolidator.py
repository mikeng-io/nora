"""Unit tests for `nora.language.exchange_consolidator.ExchangeConsolidator` —
mirrors `tests/unit/test_language_consolidator.py`'s structure exactly. The
full contract (idempotency, scope isolation against a real store) lives in
`tests/contract/test_room_consolidator_contract.py`.
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.exchange_consolidator import (
    ExchangeConsolidator,
    ExtractionUnparseableError,
)
from nora.language.exchange_observer import ExchangePatternObserver

pytestmark = pytest.mark.asyncio


class _FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, prompt: str) -> str:
        return self._responses.pop(0)


class _FakeStore:
    """Mirrors `tests/unit/test_language_consolidator.py::_FakeStore` — a
    natural-keyed `append_observations`, `rescore_calls` recorded by scope."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, tuple[int, ...]]] = set()
        self.rescore_calls: list[str] = []

    async def append_observations(self, *, scope_id, observations, batch) -> int:
        appended = 0
        for obs in observations:
            key = (scope_id, obs.act, obs.message_ids)
            if key in self._seen:
                continue
            self._seen.add(key)
            appended += 1
        return appended

    async def rescore(self, *, scope_id, now_ms) -> None:
        self.rescore_calls.append(scope_id)


def _row(mid: int, person_id: str, body: str) -> TranscriptRow:
    return TranscriptRow(
        id=mid, sender_id=person_id, sender_name=person_id, ts=1, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id=person_id,
    )


def _pattern_response(message_ids: list[int], act: str = "banter") -> str:
    return json.dumps({"patterns": [
        {"act": act, "message_ids": message_ids, "confidence": 0.9},
    ]})


async def test_an_empty_batch_appends_nothing_makes_no_model_call_but_still_rescores():
    store = _FakeStore()
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(_FakeModel(responses=[])), store=store)
    assert await consolidator.consolidate(scope_id="g1", batch=[]) == 0
    assert store.rescore_calls == ["g1"]


async def test_a_batch_yielding_a_pattern_appends_it_and_then_rescores():
    store = _FakeStore()
    batch = [_row(1, "miles", "you idiot"), _row(2, "tyler", "you're worse")]
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(
            _FakeModel(responses=[_pattern_response([1, 2])])),
        store=store,
    )
    appended = await consolidator.consolidate(scope_id="g1", batch=batch)
    assert appended == 1
    assert store.rescore_calls == ["g1"]


async def test_rescore_runs_even_when_nothing_was_appended():
    store = _FakeStore()
    batch = [_row(1, "miles", "食咩好"), _row(2, "tyler", "隨便啦")]
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(
            _FakeModel(responses=['{"patterns": []}'])),
        store=store,
    )
    appended = await consolidator.consolidate(scope_id="g1", batch=batch)
    assert appended == 0
    assert store.rescore_calls == ["g1"]


async def test_replaying_a_batch_does_not_inflate_the_rendered_count():
    store = _FakeStore()
    batch = [_row(1, "miles", "you idiot"), _row(2, "tyler", "you're worse")]
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(_FakeModel(
            responses=[_pattern_response([1, 2]), _pattern_response([1, 2])])),
        store=store,
    )
    first = await consolidator.consolidate(scope_id="g1", batch=batch)
    second = await consolidator.consolidate(scope_id="g1", batch=batch)
    assert first == 1
    assert second == 0, "the natural key must make a re-run a no-op"


async def test_an_unparseable_response_raises_and_never_appends_or_rescores():
    store = _FakeStore()
    batch = [_row(1, "miles", "x"), _row(2, "tyler", "y")]
    consolidator = ExchangeConsolidator(
        observer=ExchangePatternObserver(_FakeModel(responses=["prose"])),
        store=store,
    )
    with pytest.raises(ExtractionUnparseableError):
        await consolidator.consolidate(scope_id="g1", batch=batch)
    assert store.rescore_calls == []
