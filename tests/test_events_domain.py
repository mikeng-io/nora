from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.events import (
    RSVP,
    Event,
    EventStatus,
    RSVPResponse,
    normalise_event,
)
from nora.domain.reminders import NaiveDatetimeError


def _event(**overrides) -> Event:
    defaults = {
        "id": uuid4(),
        "conversation_id": "signal:group-1",
        "title": "Dinner",
        "created_by": "miles",
        "when": None,
        "venue_text": None,
        "venue_place_id": None,
        "venue_name": None,
        "venue_address": None,
        "venue_rating": None,
        "venue_price_level": "",
        "venue_maps_url": None,
        "venue_resolved_at": None,
        "status": EventStatus.PROPOSED,
        "reminder_id": None,
        "created_at": datetime(2026, 8, 19, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Event(**defaults)


def test_normalise_event_accepts_utc_aware_datetimes():
    ev = _event(when=datetime(2026, 8, 22, 19, 0, tzinfo=UTC))
    out = normalise_event(ev)
    assert out.when == ev.when


def test_normalise_event_rejects_naive_when():
    ev = _event(when=datetime(2026, 8, 22, 19, 0))  # naive
    with pytest.raises(NaiveDatetimeError):
        normalise_event(ev)


def test_rsvp_response_values():
    assert RSVPResponse.YES.value == "yes"
    assert RSVPResponse.NO.value == "no"
    assert RSVPResponse.MAYBE.value == "maybe"


def test_event_status_values():
    assert EventStatus.PROPOSED.value == "proposed"
    assert EventStatus.CONFIRMED.value == "confirmed"
    assert EventStatus.CANCELLED.value == "cancelled"


def test_rsvp_construction():
    r = RSVP(
        event_id=uuid4(), person_id="miles",
        response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert r.response is RSVPResponse.YES
