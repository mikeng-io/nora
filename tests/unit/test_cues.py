"""`nora.domain.cues.cues_of` — the deterministic cues,
in English and in Cantonese, and the ones it must NOT see.

Phrases here are generic examples of how people in a group chat say these
things, not room messages."""

from __future__ import annotations

import pytest

from nora.domain.cues import CUES, STRONG_CUES, cues_of, is_strong


@pytest.mark.parametrize("text", [
    # Cantonese / Chinese date and time expressions
    "聽日食飯", "今晚得唔得", "後日先", "禮拜六見", "星期日下晝", "下個禮拜",
    "7點半", "七點", "三點鐘", "幾點", "9月28號", "28號", "今晚7:30", "聽朝",
    # English
    "dinner tomorrow", "tonight?", "at 8", "7pm", "19:30", "next friday",
    "Sep 28", "28th of september", "28/9", "2026-09-28", "in 20 mins",
])
def test_date_and_time_expressions_carry_the_datetime_cue(text):
    assert "datetime" in cues_of(text)


@pytest.mark.parametrize("text", [
    "$300 each", "HK$120", "每人300蚊", "夾錢", "AA制", "let's split it",
    "you owe me", "200 hkd",
])
def test_amounts_and_splits_carry_the_amount_cue(text):
    assert "amount" in cues_of(text)


@pytest.mark.parametrize("text", [
    "記低", "記低呢個", "keep that", "write that down", "note this down",
    "記住呢個", "Nora 記住：wifi 係 orchid-19", "remember this", "the code is 4471, remember that",
])
def test_keep_instructions_carry_the_keep_cue(text):
    assert "keep" in cues_of(text)


@pytest.mark.parametrize("text", [
    "記住啦你 😤", "我唔記住喇", "我記住咗喇", "remember that time we got lost?",
    "do you remember the trip",
])
def test_banter_and_reminiscing_carry_no_keep_cue(text):
    """DEC-306: the widened keep cue stays narrow; a cue only wakes a
    session, but a wake per joke is a model call paid for nothing."""
    assert "keep" not in cues_of(text)


@pytest.mark.parametrize("text", [
    "I'm in", "count me in", "can't make it", "+1", "我去", "去唔到", "唔得閒",
    "算我一份",
])
def test_rsvp_words_carry_the_rsvp_cue(text):
    assert "rsvp" in cues_of(text)


@pytest.mark.parametrize("text", [
    "https://maps.app.goo.gl/abc123", "https://www.google.com/maps/place/x",
    "https://www.openrice.com/zh/hongkong/r-x", "https://maps.apple.com/?q=x",
])
def test_place_links_carry_the_place_link_cue(text):
    assert "place_link" in cues_of(text)


@pytest.mark.parametrize("text", [
    "don't tell her", "it's a surprise", "唔好話俾佢知", "唔好同佢講", "秘密",
])
def test_surprise_phrases_carry_the_surprise_cue(text):
    assert "surprise" in cues_of(text)


@pytest.mark.parametrize("text", [
    "", "hello", "haha", "lol ok", "I sat down", "the sun is out", "好攰",
    "https://example.com/page", "[image a3f2c1]",
])
def test_ordinary_chat_carries_no_cue(text):
    """The negative half: a cue that fires on everything is no cue."""
    assert cues_of(text) == ()


def test_full_width_forms_are_folded_first():
    assert "datetime" in cues_of("今晚７：３０")


def test_cues_come_back_in_declared_order_once_each():
    got = cues_of("記低: dinner tomorrow at 8, $300 each, I'm in 聽日")
    assert got == ("datetime", "amount", "keep", "rsvp")
    assert list(got) == [c for c in CUES if c in got]


def test_strong_cues_are_a_subset_and_amount_and_surprise_are_not_strong():
    assert set(CUES) >= STRONG_CUES
    assert not is_strong(("amount", "surprise"))
    assert is_strong(("amount", "datetime"))
