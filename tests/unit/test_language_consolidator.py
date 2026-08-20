"""Unit tests for `nora.language.consolidator.LanguageConsolidator` — the
sequencing shim over `LexemeObserver` (Task 6) and `SqlLanguageStore` (Task
7). The full contract this class must satisfy (idempotency against a real
store, scope isolation) lives in
`tests/contract/test_room_consolidator_contract.py`, which now runs this
class against a real `SqlLanguageStore`. These tests are the ones specific to
THIS class's own responsibility — the order it calls its two collaborators
in, and what it returns — using lightweight same-process fakes for both, the
same `FakeModel` shape `tests/unit/test_language_observer.py` already uses.
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.consolidator import ExtractionUnparseableError, LanguageConsolidator
from nora.language.observer import LexemeObserver

pytestmark = pytest.mark.asyncio


class _FakeModel:
    """Same shape as `tests/unit/test_language_observer.py::FakeModel`:
    `responses` is consumed in order, one per `observe()` call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.model = "fake-consolidator-model"

    async def complete(self, *, prompt: str) -> str:
        return self._responses.pop(0)


class _FakeStore:
    """Stands in for `SqlLanguageStore`'s write side. `append_observations`
    is natural-keyed on `(message_id, canonical_form, sense)`, mirroring the
    real store's `ON CONFLICT DO NOTHING` (CLAUDE.md: a fake must behave like
    the real server, wrong-call shapes included — a fake that just appended
    every call would make the replay test below decorative). `rescore_calls`
    is recorded, not just counted, so a test can assert BOTH that it ran and
    which scope it ran for."""

    def __init__(self) -> None:
        self._seen: set[tuple[int, str, str]] = set()
        self.rescore_calls: list[str] = []

    async def append_observations(self, *, scope_id, observations) -> int:
        appended = 0
        for obs in observations:
            key = (obs.message_id, obs.canonical_form, obs.sense)
            if key in self._seen:
                continue
            self._seen.add(key)
            appended += 1
        return appended

    async def rescore(self, *, scope_id, now_ms) -> None:
        self.rescore_calls.append(scope_id)


def _row(id: int, body: str) -> TranscriptRow:
    return TranscriptRow(
        id=id, sender_id="+852111", sender_name="Miles", ts=1, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id="miles",
    )


def _observation_response(message_id: int, canonical_form: str = "kam") -> str:
    return json.dumps({"observations": [
        {"surface_form": canonical_form, "canonical_form": canonical_form,
         "sense": "尷", "part_of_speech": "adjective", "message_id": message_id,
         "confidence": 0.9},
    ]})


async def test_an_empty_batch_appends_nothing_makes_no_model_call_but_still_rescores():
    """Pins the coordinator's ruling on this exact class: an empty batch must
    skip the observer (`_FakeModel` with an empty `responses` list makes "no
    model call" mechanically enforced — any call to `complete` raises
    `IndexError` from `list.pop` on empty, mirroring
    `test_an_empty_batch_makes_no_model_call` in
    `tests/unit/test_language_observer.py`) AND still call `rescore`
    unconditionally. `dream_loop`'s 24h firing condition arrives here with an
    EMPTY batch specifically to fade a dormant room's lexicon — an early
    `return 0` that skipped `rescore` too would make that whole path a no-op
    and strand a stale lexicon rendering as `live` forever. Reverting the
    unconditional `rescore` call (restoring an `if not batch: return 0`
    early-out) must turn this red."""
    store = _FakeStore()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(_FakeModel(responses=[])), store=store)
    assert await consolidator.consolidate(scope_id="g1", batch=[]) == 0
    assert store.rescore_calls == ["g1"]


async def test_a_batch_yielding_a_candidate_appends_it_and_then_rescores():
    store = _FakeStore()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(_FakeModel(responses=[_observation_response(1)])),
        store=store)
    appended = await consolidator.consolidate(scope_id="g1", batch=[_row(1, "好撚kam")])
    assert appended == 1
    assert store.rescore_calls == ["g1"]


async def test_rescore_runs_even_when_nothing_was_appended():
    """Decay is time-driven — a non-empty batch that yields no NEW candidate
    (the model found nothing novel) must still let `rescore` run, or a
    dormant room's lexicon can never fade. This is the regression
    `dream_loop`'s 24h firing condition depends on."""
    store = _FakeStore()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(_FakeModel(responses=['{"observations": []}'])),
        store=store)
    appended = await consolidator.consolidate(scope_id="g1", batch=[_row(1, "普通話")])
    assert appended == 0
    assert store.rescore_calls == ["g1"]


async def test_replaying_a_batch_does_not_inflate_the_rendered_count():
    """The failure this prevents is not a crash — it is `12x, 3 people`
    silently becoming `24x, 3 people`, which reads as evidence and is not."""
    store = _FakeStore()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(_FakeModel(
            responses=[_observation_response(1), _observation_response(1)])),
        store=store)
    batch = [_row(1, "好撚kam")]
    first = await consolidator.consolidate(scope_id="g1", batch=batch)
    second = await consolidator.consolidate(scope_id="g1", batch=batch)
    assert first == 1
    assert second == 0, "the natural key must make a re-run a no-op"


async def test_an_unparseable_response_raises_and_never_appends_or_rescores():
    """Finding 2 (final whole-branch review): a model returning 200-OK prose
    for a non-empty batch used to make `consolidate` return `0` — exactly the
    same value a well-formed, genuinely empty extraction returns
    (`test_rescore_runs_even_when_nothing_was_appended` above) — and
    `nora.app.runtime._dream_once` cannot tell the two apart from an int
    alone. `_dream_once` advances the watermark whenever `batch` is
    non-empty, so that batch's messages were being consumed FOREVER on every
    parse failure. `ExtractionUnparseableError` is what makes the two
    distinguishable: raising (rather than returning `0`) is what lets
    `_dream_once`'s own crash-path — already proven not to advance the
    watermark
    (`tests/unit/test_runtime.py::
    test_the_watermark_advances_only_after_observations_are_committed`) —
    apply here too. Reverting `consolidate`'s `if not result.parsed: raise
    ...` guard back to falling straight through to `append_observations`
    (which appends nothing for an unparsed batch either way) must turn this
    red: no exception, and `rescore` would run."""
    store = _FakeStore()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(_FakeModel(responses=["sorry, can't help with that"])),
        store=store)
    with pytest.raises(ExtractionUnparseableError):
        await consolidator.consolidate(scope_id="g1", batch=[_row(1, "好撚kam")])
    assert store.rescore_calls == [], (
        "an unparseable batch must not even rescore — the whole cycle is untrusted"
    )
