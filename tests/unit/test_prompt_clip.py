"""`nora.domain.prompt.clip` and `legal`, the two helpers shared by the
Cognition renderer and `escape`."""

from __future__ import annotations

from nora.domain.prompt import clip, escape_attr, legal, one_line


def test_a_short_text_is_only_collapsed():
    assert clip("a\n b\n\n c", 400) == "a b c"
    assert clip("y" * 400, 400) == "y" * 400


def test_a_long_text_is_cut_with_an_ellipsis_at_the_limit():
    assert clip("y" * 401, 400) == "y" * 399 + "…"
    assert clip("one two three", 7) == "one tw…"


def test_a_newline_cannot_survive_the_cut():
    assert "\n" not in clip("ok\n" + "x" * 50, 10)


def test_a_limit_below_two_still_keeps_a_character():
    assert clip("hello", 1) == "h…"
    assert clip("hello", 0) == "h…"


def test_legal_removes_what_xml_cannot_carry_and_escape_attr_applies_it():
    assert legal("a\x0bb\x01c\td") == "abc\td"
    assert escape_attr("a\x01b") == "ab"
    assert one_line(legal("a\x01 b")) == "a b"
