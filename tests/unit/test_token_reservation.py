"""Offline probes for the historical session's token reservation hook."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nora.agent.token_reservation import TokenReservation


class _Model:
    def get_config(self):
        return {"params": {"max_tokens": 20}}


def _before(reservation: TokenReservation, *, estimate: int = 100):
    event = SimpleNamespace(
        cancel=False,
        projected_input_tokens=estimate,
        agent=SimpleNamespace(model=_Model(), trace_attributes={}),
    )
    reservation.before(event)
    return event


def test_an_unaffordable_first_call_is_refused_before_the_provider_call():
    reservation = TokenReservation(129)
    event = _before(reservation, estimate=100)
    assert event.cancel == "limit_total_tokens"
    assert reservation.stopped is True
    assert reservation.spent == 0


def test_unknown_usage_stops_the_session_instead_of_spending_against_an_unread_balance():
    reservation = TokenReservation(1000)
    event = _before(reservation, estimate=100)
    assert not event.cancel
    after = SimpleNamespace(
        stop_response=SimpleNamespace(message={"metadata": {"usage": {}}}),
        retry=True,
    )
    with pytest.raises(RuntimeError, match="usage unknown"):
        reservation.after(after)
    assert reservation.spent is None
    assert after.retry is False


def test_cancelled_in_flight_model_usage_remains_unknown():
    reservation = TokenReservation(1000)
    event = _before(reservation, estimate=100)
    assert not event.cancel
    assert reservation.spent is None

