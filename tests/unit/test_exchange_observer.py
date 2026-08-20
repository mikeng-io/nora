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
from nora.domain.provenance import instruction_sha256
from nora.language.exchange_observer import ExchangePatternObserver
from nora.language.observer import LexemeObserver
from nora.obs.metrics import Metrics
from tests.objectives import (
    PRE_MOVE,
    append_to_objective,
    editable_objectives,
    shipped_objectives,
)

# The `objective` collaborator, wired the way production wires it: the REAL
# `DreamerInstructions` over the REAL `nora/dreamer/` tree, not a double. The
# bodies it serves are byte-identical to the constants these tests were written
# against (`tests/unit/test_dreamer_instructions.py` proves it), so nothing
# below changed meaning — and a broken objective file now fails here rather
# than on the next Dreamer tick.
OBJECTIVES = shipped_objectives()

pytestmark = pytest.mark.asyncio


# See `tests/unit/test_language_observer.py::FAKE_MODEL`. Deliberately a
# DIFFERENT name from the lexeme suite's: `[language].exchange_model` lets
# this adaptor run its own model, and a shared constant would hide a stamp
# that read the wrong client.
FAKE_MODEL = "fake-exchange-model-v1"


class FakeModel:
    def __init__(self, responses: list[str], *, model: str = FAKE_MODEL) -> None:
        self._responses = list(responses)
        self.model = model

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
    observer = ExchangePatternObserver(
        FakeModel(responses=['{"patterns": []}']),
        objective=OBJECTIVES,
    )
    result = await observer.observe(batch=rows((1, "miles", "食咩好")))
    assert result.candidates == ()
    assert result.parsed is True


async def test_malformed_json_yields_nothing_rather_than_raising():
    observer = ExchangePatternObserver(
        FakeModel(responses=["not json at all"]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert result.candidates == ()
    assert result.parsed is False


async def test_an_act_outside_the_closed_set_is_dropped():
    """DEC-120's first structural guarantee: an `act` the model invents,
    rather than selecting from `EXCHANGE_ACTS`, must never reach a
    candidate — this is what makes a free-text disposition claim
    unconstructible."""
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("miles_is_dumb", [1, 2])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert result.candidates == ()
    assert result.parsed is True, "the response parsed; one item was rejected"


async def test_a_pattern_with_fewer_than_two_message_ids_is_dropped():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("banter", [1])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(batch=rows((1, "miles", "x")))
    assert result.candidates == ()


async def test_a_pattern_whose_two_message_ids_are_the_same_speaker_is_dropped():
    """DEC-120's second structural guarantee, exercised at the join step:
    two message ids naming only ONE distinct person describe no exchange at
    all — a single participant is exactly the "disposition about a person"
    shape the withhold rule forbids."""
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("banter", [1, 2])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(
        batch=rows((1, "miles", "haha"), (2, "miles", "lol")))
    assert result.candidates == ()


async def test_a_genuine_two_person_pattern_carries_both_participants():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("insult_trade", [1, 2])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))
    assert result.parsed is True
    [c] = result.candidates
    assert c.act == "insult_trade"
    assert set(c.participants) == {"miles", "tyler"}


async def test_a_pattern_naming_a_message_not_in_the_batch_is_dropped():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("banter", [1, 999])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(batch=rows((1, "miles", "x")))
    assert result.candidates == ()
    assert result.parsed is True


async def test_an_empty_batch_makes_no_model_call():
    observer = ExchangePatternObserver(
        FakeModel(responses=[]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(batch=[])
    assert result.candidates == ()
    assert result.parsed is True


async def test_a_parse_failure_is_counted_distinctly_from_an_empty_extraction():
    metrics = Metrics()
    prose = ExchangePatternObserver(
        FakeModel(responses=["prose"]), metrics=metrics,
        objective=OBJECTIVES,
    )
    await prose.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert metrics.get("nora_exchange_parse_failures_total") == 1

    empty = ExchangePatternObserver(
        FakeModel(responses=['{"patterns": []}']), metrics=metrics,
        objective=OBJECTIVES,
    )
    await empty.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    assert metrics.get("nora_exchange_parse_failures_total") == 1


async def test_a_single_participant_drop_is_counted_distinctly():
    metrics = Metrics()
    observer = ExchangePatternObserver(
        FakeModel(responses=[_pattern("banter", [1, 2])]), metrics=metrics,
        objective=OBJECTIVES,
    )
    await observer.observe(batch=rows((1, "miles", "a"), (2, "miles", "b")))
    assert metrics.get(
        "nora_exchange_candidates_dropped_total", reason="single_participant",
    ) == 1


# ── instruction provenance (2026-08-20 instruction-substrate spec §4.3) ──────
#
# The lexeme sibling of these tests carries the argument
# (`tests/unit/test_language_observer.py`). `act` here is drawn from a closed
# set, which bounds what a row can SAY but not which instruction chose it.

async def test_every_candidate_carries_the_instruction_that_produced_it():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("insult_trade", [1, 2])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))
    [c] = result.candidates
    assert c.instruction_sha256 == observer.instruction_sha256
    assert len(c.instruction_sha256) == 64, "a real digest, not a placeholder"


async def test_the_stamp_is_the_digest_of_the_instruction_actually_sent():
    """Bound to the real property, not a proxy: the digest is over the
    `room-exchange` objective BODY, so a row's stamp is reproducible by hand
    from the file with `sha256sum` on everything after the frontmatter. An
    observer that hashed a version number, or the assembled per-batch prompt
    (which varies with the transcript and would give every batch its own
    digest), would pass a weaker assertion and fail this one."""
    observer = ExchangePatternObserver(
        FakeModel(responses=[]),
        objective=OBJECTIVES,
    )
    assert observer.instruction_sha256 == instruction_sha256(
        OBJECTIVES.objective_body("room-exchange")
    )


async def test_the_two_adaptors_stamp_different_digests():
    """Two adaptors, two instruction sets — a shared digest would make a row
    unattributable to the adaptor that wrote it, which is half the question
    «which instruction produced this»."""
    assert (
        ExchangePatternObserver(
            FakeModel(responses=[]),
            objective=OBJECTIVES,
        ).instruction_sha256
        != LexemeObserver(
            FakeModel(responses=[]),
            objective=OBJECTIVES,
        ).instruction_sha256
    )


async def test_editing_the_instruction_text_changes_every_row_it_writes(tmp_path):
    """The whole point of the column: two corpora written either side of an
    instruction edit must be distinguishable. The edit here is a real one — a
    real objective file on disk, rewritten and re-read through the real reload
    machine, which is what production does. ONE observer across both, because
    the hot-reload half is the half that could regress: an observer that
    captured the text at construction would keep the old digest forever and
    every row after the edit would lie about what wrote it."""
    objectives, root = editable_objectives(tmp_path)
    before = ExchangePatternObserver(FakeModel(
        responses=[_pattern("insult_trade", [1, 2]),
                   _pattern("insult_trade", [1, 2])]),
        objective=objectives,
    )
    result_before = await before.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))
    digest_before = before.instruction_sha256

    append_to_objective(root, "room-exchange", "One exchange per pair, at most.\n")

    after = before
    result_after = await after.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))

    assert digest_before != after.instruction_sha256
    [c_before] = result_before.candidates
    [c_after] = result_after.candidates
    assert c_before.instruction_sha256 != c_after.instruction_sha256


# ── model provenance (2026-08-20 substrate spec §6.2 question 2) ─────────────

async def test_every_candidate_carries_the_model_that_produced_it():
    observer = ExchangePatternObserver(FakeModel(
        responses=[_pattern("insult_trade", [1, 2])]),
        objective=OBJECTIVES,
    )
    result = await observer.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))
    [c] = result.candidates
    assert c.model == observer.model
    assert c.model == FAKE_MODEL


async def test_the_stamped_model_is_the_one_the_client_reports():
    observer = ExchangePatternObserver(
        FakeModel(responses=[_pattern("insult_trade", [1, 2])], model="m-exchange"),
        objective=OBJECTIVES,
    )
    result = await observer.observe(
        batch=rows((1, "miles", "you idiot"), (2, "tyler", "you're worse")))
    [c] = result.candidates
    assert observer.model == "m-exchange"
    assert c.model == "m-exchange"


async def test_the_two_adaptors_can_stamp_different_models():
    """`[language].exchange_model` lets this adaptor run its own model, so two
    rows written on the SAME tick for the same room may honestly name
    different models — an observer reading a single shared config value could
    not express that."""
    assert (
        ExchangePatternObserver(
            FakeModel(responses=[], model="m-exchange"),
            objective=OBJECTIVES,
        ).model
        != LexemeObserver(
            FakeModel(responses=[], model="m-lexeme"),
            objective=OBJECTIVES,
        ).model
    )


# --- 7.1 step (a): the prompt did not move --------------------------------


class _RecordingModel:
    """`FakeModel` that keeps every prompt it was handed."""

    model = FAKE_MODEL

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    async def complete(self, *, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


async def test_the_prompt_still_starts_with_the_pre_move_text():
    """The end-to-end half of the byte-identity gate: what reaches the MODEL,
    not just what is on disk."""
    model = _RecordingModel(['{"patterns": []}'])
    observer = ExchangePatternObserver(model, objective=OBJECTIVES)
    await observer.observe(batch=rows((1, "miles", "x"), (2, "tyler", "y")))
    before = (PRE_MOVE / "room-exchange.txt").read_text(encoding="utf-8")
    assert model.prompts[0].startswith(before)
