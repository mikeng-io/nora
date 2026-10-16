"""`<spoken_turn>`: the per-turn
situation fact that the message being answered was spoken in a voice channel and
the reply will be heard.

Pinned: it renders for a spoken message and not for a typed one; it lives in the
per-turn block message, never in the cached header or the tag message; it is
the last block, directly before the message it describes.
"""

from __future__ import annotations

from nora.agent.prefill import build_prefill, build_tag_message
from nora.domain.framing import SPOKEN_TURN
from nora.domain.models import NormalizedMessage, TagContext


def _ctx(*, voice: bool) -> TagContext:
    msg = NormalizedMessage("discord", "g1", "group", "123", None, 1,
                            "nora 今晚食咩好", {}, voice=voice)
    return TagContext("tag-1", msg, None, (), (), 0.0)


def _prefill(*, voice: bool) -> list[dict]:
    return build_prefill(_ctx(voice=voice), display_name="Nora", context_window=40)


def _text(message: dict) -> str:
    content = message["content"]
    return content if isinstance(content, str) else content[0]["text"]


def test_a_spoken_message_carries_the_marker_in_the_per_turn_message():
    header, blocks, _closing = _prefill(voice=True)
    body = _text(blocks)
    assert "<spoken_turn " in body
    assert SPOKEN_TURN in body
    # Last block: nothing between it and the message it describes.
    assert body.rstrip().endswith("</spoken_turn>")
    # Never the cached head, and never folded into the member's own words.
    assert "spoken_turn" not in _text(header)
    tag = build_tag_message(_ctx(voice=True), tagger_name="Miles", display_name="Nora")
    assert "spoken_turn" not in tag


def test_a_typed_message_carries_no_marker_at_all():
    for message in _prefill(voice=False):
        assert "spoken_turn" not in _text(message)
        assert SPOKEN_TURN not in _text(message)


def test_the_header_is_byte_identical_spoken_or_typed():
    """The cached prefix must not move with the surface."""
    assert _text(_prefill(voice=True)[0]) == _text(_prefill(voice=False)[0])
