# tests/test_event_tools.py (this file also gets the doctrine test in Task 10 — start it now)
from __future__ import annotations

from nora.agent.tools import NATIVE_TOOLS


def test_all_six_event_tools_are_registered():
    for name in (
        "create_event", "update_event", "rsvp_event",
        "cancel_event", "list_events", "suggest_venue",
    ):
        assert name in NATIVE_TOOLS, f"{name} missing from NATIVE_TOOLS"
