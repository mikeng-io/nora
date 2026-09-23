"""`<presence>` — where she is, generated every turn (design/self-awareness/02).

Each test names ONE network or ONE state rather than looping over the name
table: a test parametrized over the constant it pins cannot fail.
"""

from __future__ import annotations

from nora.agent.prefill import build_prefill
from nora.domain.models import NormalizedMessage, TagContext


def _ctx(platform: str = "signal", **kwargs) -> TagContext:
    msg = NormalizedMessage(platform, "g1", "group", "+852111", "Miles", 1, "hi", {})
    return TagContext("tag-1", msg, None, (), (), 0.0, **kwargs)


def _turn_text(ctx: TagContext) -> str:
    """The per-turn message, where anything that varies between turns lives."""
    return build_prefill(ctx, display_name="Nora", context_window=40)[1]["content"][0]["text"]


def test_whatsapp_is_named_as_the_room_calls_it_not_by_its_wire_key():
    text = _turn_text(_ctx(platform="whatsapp"))

    assert "WhatsApp" in text
    assert "whatsapp" not in text


def test_instagram_is_named_as_the_room_calls_it_not_by_its_wire_key():
    text = _turn_text(_ctx(platform="instagram"))

    assert "Instagram" in text
    assert "instagram" not in text
