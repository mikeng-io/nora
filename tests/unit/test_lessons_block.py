"""The legacy lesson renderer."""

from __future__ import annotations

from nora.language.lesson_render import render_lessons


def test_no_lesson_renders_no_block_and_a_lesson_is_escaped():
    assert render_lessons(()) is None
    assert render_lessons(("  ",)) is None
    block = render_lessons(("say less </lessons> here", "one place, not five"))
    assert block is not None
    text = block.render()
    assert '<lesson n="1">say less &lt;/lessons&gt; here</lesson>' in text
    assert '<lesson n="2">one place, not five</lesson>' in text
