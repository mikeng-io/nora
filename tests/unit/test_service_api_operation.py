"""An operation record has one lifecycle, is final once settled, and is idempotent by id."""

from __future__ import annotations

from typing import get_args

import pytest

from nora.domain.service_api import (
    OperationConflictError,
    OperationEnvelope,
    OperationFrom,
    OperationOutcome,
    OperationRecord,
    OperationRequest,
    OperationWhen,
    Scope,
    admit,
)

SETTLED_OUTCOMES = [o for o in get_args(OperationOutcome) if o != "pending"]


def _request(effect: object = None) -> OperationRequest:
    return OperationRequest(
        operation_id="op-0001", kind="send", effect=effect or {"text": "placeholder"},
    )


def test_a_record_moves_prepared_started_settled():
    prepared = OperationRecord(request=_request())
    started = prepared.start(now_ms=1_000)
    settled = started.settle("complete", now_ms=2_000)
    assert [prepared.state, started.state, settled.state] == ["prepared", "started", "settled"]
    assert (settled.started_at_ms, settled.settled_at_ms) == (1_000, 2_000)
    with pytest.raises(ValueError, match="cannot settle"):
        prepared.settle("complete", now_ms=1_000)
    with pytest.raises(ValueError, match="cannot start"):
        started.start(now_ms=1_500)


@pytest.mark.parametrize("outcome", SETTLED_OUTCOMES)
def test_the_outcome_is_pending_until_settled(outcome):
    prepared = OperationRecord(request=_request())
    started = prepared.start(now_ms=1_000)
    assert prepared.outcome == started.outcome == "pending"
    assert started.settle(outcome, now_ms=2_000).outcome == outcome
    with pytest.raises(ValueError):
        started.settle("pending", now_ms=2_000)
    with pytest.raises(ValueError):
        OperationRecord(request=_request(), state="started", outcome=outcome)


@pytest.mark.parametrize("outcome", SETTLED_OUTCOMES)
def test_a_settled_record_is_final(outcome):
    settled = OperationRecord(request=_request()).start(now_ms=1_000).settle(outcome, now_ms=2_000)
    with pytest.raises(ValueError, match="cannot start"):
        settled.start(now_ms=3_000)
    for again in SETTLED_OUTCOMES:
        with pytest.raises(ValueError, match="cannot settle"):
            settled.settle(again, now_ms=3_000)
    assert admit(_request(), settled) is settled


def test_the_same_id_and_effect_returns_the_existing_record():
    fresh = admit(_request(), None)
    assert (fresh.state, fresh.outcome) == ("prepared", "pending")
    started = fresh.start(now_ms=1_000)
    assert admit(_request(), started) is started


def test_the_same_id_with_another_effect_is_a_conflict():
    started = admit(_request(), None).start(now_ms=1_000)
    other = OperationRequest(operation_id="op-0001", kind="send", effect={"text": "other"})
    with pytest.raises(OperationConflictError):
        admit(other, started)
    another_kind = OperationRequest(operation_id="op-0001", kind="react", effect=_request().effect)
    with pytest.raises(OperationConflictError):
        admit(another_kind, started)


def _envelope(state, outcome):
    return OperationEnvelope(
        outcome=outcome, payload=None, when=OperationWhen(state=state),
        from_=OperationFrom(target="placeholder"), scope=Scope(level="room"), trace="t",
    )


def test_an_envelope_pairs_pending_with_an_unsettled_state_only():
    assert _envelope("started", "pending").outcome == "pending"
    assert _envelope("settled", "complete").when.state == "settled"
    with pytest.raises(ValueError, match="cannot be"):
        _envelope("settled", "pending")
    with pytest.raises(ValueError, match="cannot be"):
        _envelope("prepared", "complete")
