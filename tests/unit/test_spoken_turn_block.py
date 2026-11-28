"""The room floor marks a spoken turn next to its trigger message."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.prefill import engagement_floor, engagement_prefill
from nora.domain.framing import SPOKEN_TURN, TAG_HEADER
from nora.domain.models import NormalizedMessage, TagContext


def _floor(*, voice: bool):
    msg = NormalizedMessage("discord", "g1", "group", "123", None, 1,
                            "nora 今晚食咩好", {}, voice=voice)
    return engagement_floor(
        TagContext("tag-1", msg, None, (), 0.0), display_name="Nora",
        tagger_name="Miles", context_window=40,
        now=datetime(2026, 10, 7, tzinfo=UTC), catalogue="",
    )


def test_a_spoken_message_carries_the_marker_in_the_per_turn_message():
    *_, spoken, trigger = _floor(voice=True)
    assert spoken.name == "spoken_turn"
    assert SPOKEN_TURN in spoken.render()
    assert trigger.name == "tag_message"
    assert "spoken_turn" not in trigger.render()


def test_a_typed_message_carries_no_marker_at_all():
    assert all("spoken_turn" not in block.render() for block in _floor(voice=False))


def test_the_cached_header_carries_no_spoken_marker():
    header = engagement_prefill(TAG_HEADER)[0]["content"][0]["text"]
    assert "spoken_turn" not in header and SPOKEN_TURN not in header
