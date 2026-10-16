"""`nora.domain.speech_markup`: the deterministic half of the speech formatter.

The formatter's model may ADD pauses and interjection tags and nothing else.
These tests are the adversary: each feeds `check` an output a cheap model
plausibly produces and that must not be spoken. Every fixture is invented
Cantonese, never a recorded reply.
"""

from __future__ import annotations

import pytest

from nora.domain.speech_markup import (
    FORMAT_OUTCOMES,
    INTERJECTIONS,
    check,
    normalise,
    spoken_skeleton,
    strip_markup,
)

_REPLY = "好呀，聽日三點喺旺角等，記得帶把遮"
_BASE = normalise(_REPLY)


# -- normalise: removes what is not read, never a letter or a digit ---------

@pytest.mark.parametrize("raw", [
    "好呀😂😂 聽日 3 點見",
    "**唔係呀** 你講到好似我成日遲到咁",
    "- 第一，買餸\n- 第二，執屋\n- 第三，覆 email",
    "冇問題～ 等我一陣 👍🏽",
    "# 今日行程\n1. 食飯\n2. 睇戲",
    "`code` 同 __底線__ 同 ~~劃走~~",
    "家庭 👨‍👩‍👧 出街 1️⃣",
    "<#0.50#> 呢個係我自己打嘅",
    # A code fence's backticks go; the words after them stay.
    "睇吓 ```python 呢段 5 行``` 先",
    "```\n一行\n```",
    "สวัสดี नमस्ते",
])
def test_normalise_never_removes_a_letter_or_a_digit(raw):
    assert spoken_skeleton(normalise(raw)) == spoken_skeleton(raw)


def test_normalise_removes_emoji_markdown_and_stray_symbols():
    got = normalise("**好呀** 😂 聽日 3 點～\n- 記得帶 RV 嘅 key 🔑")
    assert got == "好呀 聽日 3 點\n記得帶 RV 嘅 key"


def test_normalise_disarms_a_pause_her_own_text_contained():
    """Only a pause `check` has seen may reach the voice: one typed into her
    reply loses its `#` and is no longer markup."""
    assert "<#" not in normalise("等陣<#5#>再講")


def test_a_unit_symbol_a_voice_says_is_kept():
    assert normalise("今日 32℃，體感 35°") == "今日 32℃，體感 35°"


def test_a_list_number_is_a_word_and_stays():
    assert normalise("1. 食飯\n2. 睇戲") == "1. 食飯\n2. 睇戲"


# -- check: what passes ------------------------------------------------------

@pytest.mark.parametrize("candidate", [
    _BASE,
    "好呀，<#0.30#>聽日三點喺旺角等，<#0.25#>記得帶把遮",
    "好呀(laughs)，聽日三點喺旺角等，記得帶把遮",
    # A pause standing where a comma was: punctuation is rhythm, not a word.
    "好呀<#0.30#>聽日三點喺旺角等<#0.20#>記得帶把遮",
])
def test_check_passes_her_words_with_markup_added(candidate):
    assert check(_BASE, candidate) is None


# -- check: what must be refused, one adversarial output each ---------------

def test_a_rewritten_word_is_refused():
    assert check(_BASE, "好呀，聽日四點喺旺角等，記得帶把遮") == "words_changed"


def test_a_dropped_word_is_refused():
    assert check(_BASE, "好呀，聽日三點喺旺角等") == "words_changed"


def test_a_reordered_reply_is_refused():
    assert check(_BASE, "記得帶把遮，好呀，聽日三點喺旺角等") == "words_changed"


def test_an_added_word_is_refused():
    assert check(_BASE, "好呀，聽日三點喺旺角等，記得帶把遮啦") == "words_changed"


def test_an_invented_interjection_is_refused():
    """(giggles) is not in MiniMax's list, so the voice would SAY «giggles»."""
    assert "giggles" not in INTERJECTIONS
    assert check(_BASE, "好呀(giggles)，聽日三點喺旺角等，記得帶把遮") == "words_changed"


def test_an_inline_pronunciation_the_model_added_is_refused():
    assert check(_BASE, "好呀，聽日三點喺旺角(wong6)等，記得帶把遮") == "words_changed"


def test_consecutive_pauses_are_refused():
    assert check(_BASE, "好呀，<#0.5#><#0.5#>聽日三點喺旺角等，記得帶把遮") == "bad_markup"


def test_a_leading_pause_is_refused():
    assert check(_BASE, "<#0.50#>" + _BASE) == "bad_markup"


def test_a_trailing_pause_is_refused():
    assert check(_BASE, _BASE + "<#0.50#>") == "bad_markup"


@pytest.mark.parametrize(
    "marker", ["<#5#>", "<#0#>", "<#0.005#>", "<#1.234#>", "<#abc#>", "<#-1#>"],
)
def test_a_pause_out_of_range_or_malformed_is_refused(marker):
    assert check(_BASE, f"好呀，{marker}聽日三點喺旺角等，記得帶把遮") == "bad_markup"


def test_a_half_written_pause_is_refused():
    assert check(_BASE, "好呀，<#0.5聽日三點喺旺角等，記得帶把遮") == "bad_markup"


def test_an_emoji_the_model_added_is_refused():
    assert check(_BASE, _BASE + "😂") == "bad_markup"


def test_too_many_interjections_are_refused():
    many = "好呀(laughs)，聽日(chuckle)三點喺旺角等(sighs)，記得帶把遮"
    assert check(_BASE, many) == "bad_markup"


def test_a_pause_after_every_word_is_refused():
    """Every stretch holds a spoken character, so only the count refuses it."""
    words = "聽日三點喺旺角等記得帶把遮"
    assert check(words, "<#0.1#>".join(words)) == "bad_markup"


@pytest.mark.parametrize(("base", "candidate"), [
    ("圓周率係 3.14", "圓周率係 314"),
    ("圓周率係 3.14", "圓周率係 3 14"),
    ("打 12345 啦", "打 12 345 啦"),
    ("今晚 -5 度", "今晚 5 度"),
    ("19:54 喇", "1954 喇"),
], ids=["decimal-dropped", "decimal-spaced", "space-in-number", "minus-dropped", "colon-dropped"])
def test_punctuation_inside_a_number_is_part_of_the_word(base, candidate):
    assert check(base, candidate) == "words_changed"


def test_a_pause_beside_a_number_is_still_allowed():
    assert check("今日 32 度，濕度 85%", "今日 32 度<#0.30#>濕度 85%") is None


def test_a_dropped_combining_mark_is_a_changed_word():
    """Thai and Devanagari carry vowels as combining marks."""
    assert check("สวัสดี ครับ", "สวสด ครับ") == "words_changed"
    assert check("नमस्ते दोस्त", "नमसत दोस्त") == "words_changed"


def test_a_pause_written_in_full_width_digits_is_refused():
    assert check("好呀聽日見記得帶遮", "好呀<#１#>聽日見記得帶遮") == "bad_markup"


def test_an_empty_answer_is_refused():
    assert check(_BASE, "   ") == "empty"


def test_a_quoted_or_explained_answer_is_refused():
    assert check(_BASE, f"Here is the reply: {_BASE}") == "words_changed"


def test_strip_markup_leaves_an_unknown_tag_in_place():
    assert strip_markup("好(giggles)呀<#0.30#>喎(laughs)") == "好(giggles)呀喎"


def test_the_outcome_vocabulary_names_every_reason_check_returns():
    for reason in ("empty", "bad_markup", "words_changed"):
        assert reason in FORMAT_OUTCOMES
