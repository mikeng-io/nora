"""`GatheringService.create_event` refuses to guess between open events."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.app.gatherings import AmbiguousOpenGatheringsError, GatheringService
from nora.testing.gatherings import FakeGatheringStore


async def test_r52_two_open_events_refuses_a_third_rather_than_guessing():
    """`create_event` fail-closes on ambiguity (more than one open event)
    rather than silently picking one or creating a duplicate."""
    svc = GatheringService(
        store=FakeGatheringStore(),
        route_source=None,
        now=lambda: datetime(2026, 8, 19, tzinfo=UTC),
    )
    await svc.create_event(
        conversation_id="signal:group-1",
        requester_person_id="miles",
        title="A",
        platform="signal",
    )
    await svc._store.update(  # force a SECOND open event without going
        # through create_event's own guard, to set up the ambiguous state
        await svc.create_event(
            conversation_id="signal:group-1",
            requester_person_id="miles",
            title="B",
            platform="signal",
        )
    )
    with pytest.raises(AmbiguousOpenGatheringsError):
        await svc.create_event(
            conversation_id="signal:group-1",
            requester_person_id="miles",
            title="C",
            platform="signal",
        )
