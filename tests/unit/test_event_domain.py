"""Pure `nora.domain.gatherings` rules migration `0074_event_revision` adds: the
event field modes, and `Gathering`/`EventRevision` construction refusing a mode
or confidence the tier does not allow. No database — the same reason
`tests/test_gatherings_domain.py` needs none."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.gatherings import (
    EVENT_FIELD_CONFIDENCES,
    EVENT_FIELD_MODES,
    Gathering,
    GatheringStatus,
    InvalidEventFieldModeError,
    validate_event_field_confidence,
    validate_event_field_mode,
)


def _event(**overrides) -> Gathering:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    defaults = {
        "id": uuid4(), "conversation_id": "signal:group-1", "title": "Dinner",
        "created_by": "miles", "when": None, "venue_text": None,
        "venue_place_id": None, "venue_name": None, "venue_address": None,
        "venue_rating": None, "venue_price_level": "", "venue_maps_url": None,
        "venue_resolved_at": None, "status": GatheringStatus.PROPOSED,
        "reminder_id": None, "created_at": now,
    }
    defaults.update(overrides)
    return Gathering(**defaults)


def test_event_field_modes_are_the_three_40_register_names() -> None:
    """Event field modes are told, deduced, abduced — never `computed`, `induced`
    or `said`, which belong to the other tiers."""
    assert {"told", "deduced", "abduced"} == EVENT_FIELD_MODES


def test_event_field_confidences_match_every_other_tier() -> None:
    assert {"low", "medium", "high"} == EVENT_FIELD_CONFIDENCES


@pytest.mark.parametrize("mode", sorted(EVENT_FIELD_MODES))
def test_every_allowed_mode_constructs(mode: str) -> None:
    validate_event_field_mode(mode)  # does not raise
    _event(reasoning=mode)  # does not raise


def test_none_mode_means_not_stated_and_always_passes() -> None:
    validate_event_field_mode(None)
    ev = _event(reasoning=None)
    assert ev.reasoning is None


@pytest.mark.parametrize("bad", ["computed", "induced", "said", "guessed", ""])
def test_a_mode_outside_the_three_is_refused(bad: str) -> None:
    with pytest.raises(InvalidEventFieldModeError):
        validate_event_field_mode(bad)
    with pytest.raises(InvalidEventFieldModeError):
        _event(reasoning=bad)


@pytest.mark.parametrize("bad", ["urgent", "certain", ""])
def test_a_confidence_outside_low_medium_high_is_refused(bad: str) -> None:
    with pytest.raises(InvalidEventFieldModeError):
        validate_event_field_confidence(bad)
    with pytest.raises(InvalidEventFieldModeError):
        _event(confidence=bad)


def test_the_full_lifecycle_has_seven_statuses() -> None:
    """Status chain: staged -> proposed -> confirmed -> happened, or
    cancelled/lapsed/evicted off that chain."""
    assert {s.value for s in GatheringStatus} == {
        "staged", "proposed", "confirmed", "happened", "cancelled", "lapsed",
        "evicted",
    }
