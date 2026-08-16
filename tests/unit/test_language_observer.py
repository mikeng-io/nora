"""Tests for `nora.language.observer.LexemeObserver` — the Dreamer's one LLM
call. Every fixture here builds a `FakeModel` that stands in for the real
model client — the SAME collaborator shape `LexemeObserver.__init__` takes
(`async def complete(self, *, prompt: str) -> str`) — and it must reproduce
the real client's WRONG-CALL SHAPES (an empty body, prose instead of JSON, a
hallucinated `message_id`), not just its clean one. A fake that only ever
returned one well-formed candidate would keep every test below green through
a regression in the prompt or the parser (CLAUDE.md's fake-test policy).
"""

from __future__ import annotations

import json

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.observer import LexemeObserver
from nora.obs.metrics import Metrics

pytestmark = pytest.mark.asyncio


class FakeModel:
    """A same-process double for the `model` collaborator. `responses` is
    consumed in order, one per `observe()` call, mirroring exactly how a real
    client answers one prompt with one completion — never a canned "always
    one clean candidate" reply."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def complete(self, *, prompt: str) -> str:
        return self._responses.pop(0)


def rows(*bodies: str) -> list[TranscriptRow]:
    """A batch of ordinary transcript rows, ids assigned in order starting
    at 1 — the shape `LexemeObserver.observe` is handed in production."""
    return [
        TranscriptRow(
            id=i, sender_id="+84164353469", sender_name="Miles", ts=1_000 + i,
            body=body, is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None, person_id=None,
        )
        for i, body in enumerate(bodies, start=1)
    ]


def rows_with_id(
    message_id: int, body: str, *, person_id: str | None = None
) -> list[TranscriptRow]:
    """One row batch, with a caller-chosen id — what the join-back tests
    need to control precisely."""
    return [
        TranscriptRow(
            id=message_id, sender_id="+84164353469", sender_name="Miles",
            ts=1_000, body=body, is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
            person_id=person_id,
        )
    ]


async def test_a_batch_of_ordinary_cantonese_yields_no_candidates():
    """The extractor must not treat normal speech as novel. A model that
    returns something for every batch would fill the lexicon with 係/唔係
    and the block would be worthless."""
    observer = LexemeObserver(FakeModel(responses=['{"observations": []}']))
    assert await observer.observe(batch=rows("今日食咩好", "隨便啦")) == []


async def test_malformed_json_yields_nothing_rather_than_raising():
    """R-5: silence beats a wrong answer. The loop must survive a model that
    returns prose, and the cycle then appends nothing rather than crashing."""
    observer = LexemeObserver(FakeModel(responses=["sorry, I can't help with that"]))
    assert await observer.observe(batch=rows("好撚kam")) == []


async def test_a_candidate_carries_its_own_message_and_speaker():
    """Provenance is not decoration — the example rendered under a gloss is
    what makes a wrong gloss visible, and it must point at a real row."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 7, "confidence": 0.9},
    ]})]))
    [c] = await observer.observe(batch=rows_with_id(7, "好撚kam", person_id="tyler"))
    assert (c.canonical_form, c.message_id, c.person_id) == ("kam", 7, "tyler")


async def test_a_candidate_naming_a_message_not_in_the_batch_is_dropped():
    """The model can hallucinate a message_id. An observation whose evidence
    is not in the batch has no evidence, and fabricated provenance is worse
    than a missing entry."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 999, "confidence": 0.9},
    ]})]))
    assert await observer.observe(batch=rows_with_id(7, "好撚kam")) == []


async def test_an_empty_batch_makes_no_model_call():
    """Zero messages is not worth a completion — the sibling of the other
    three tests' `[...]`/prose responses, checking the OTHER short-circuit:
    `observe` must never even touch `model.complete` for an empty batch. A
    `FakeModel` with an EMPTY `responses` list makes this mechanically
    enforced — any call to `complete` raises `IndexError` (`list.pop` on
    empty), not just an assertion after the fact."""
    observer = LexemeObserver(FakeModel(responses=[]))
    assert await observer.observe(batch=[]) == []


async def test_a_dropped_candidate_is_counted_on_the_no_evidence_reason():
    """The drop in `test_a_candidate_naming_a_message_not_in_the_batch_is_dropped`
    must be OBSERVABLE, not just silent — `nora_language_candidates_dropped_total
    {reason="no_evidence"}` is the only trace a hallucinated message_id leaves.
    Reverting the counter call (but keeping the drop) must turn this red."""
    metrics = Metrics()
    observer = LexemeObserver(
        FakeModel(responses=[json.dumps({"observations": [
            {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
             "part_of_speech": "adjective", "message_id": 999, "confidence": 0.9},
        ]})]),
        metrics=metrics,
    )
    await observer.observe(batch=rows_with_id(7, "好撚kam"))
    assert metrics.get("nora_language_candidates_dropped_total", reason="no_evidence") == 1
