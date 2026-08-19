# tests/test_event_tools.py (this file also gets the doctrine test in Task 10 — start it now)
from __future__ import annotations

import pytest

from nora.agent.tools import NATIVE_TOOLS


def test_all_six_event_tools_are_registered():
    for name in (
        "create_event", "update_event", "rsvp_event",
        "cancel_event", "list_events", "suggest_venue",
    ):
        assert name in NATIVE_TOOLS, f"{name} missing from NATIVE_TOOLS"


async def test_r52_two_open_events_refuses_a_third_rather_than_guessing():
    """R-52: create_event fail-closes on ambiguity — spec §3.1's
    'more than one open event' case — rather than silently picking one or
    creating a duplicate."""
    from datetime import UTC, datetime

    from nora.app.events import AmbiguousOpenEventsError, EventService
    from nora.domain.reminders import ReminderCaps
    from nora.testing.events import FakeEventStore
    from nora.testing.reminders import FakeReminderStore

    svc = EventService(
        store=FakeEventStore(), reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(), route_source=None, honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, tzinfo=UTC),
    )
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="A",
    )
    await svc._store.update(  # force a SECOND open event without going
        # through create_event's own guard, to set up the ambiguous state
        await svc.create_event(
            conversation_id="signal:group-1", requester_person_id="miles",
            title="B",
        )
    )
    with pytest.raises(AmbiguousOpenEventsError):
        await svc.create_event(
            conversation_id="signal:group-1", requester_person_id="miles",
            title="C",
        )
