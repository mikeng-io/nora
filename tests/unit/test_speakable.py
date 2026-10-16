"""`speakable()` — the fail-closed content gate for TTS. Anything that reads as a long opaque
string stays a machine token; the bias is deliberate: a false negative costs one reply its voice,
a false positive reads a 40-character token aloud in a shared channel.
"""

from __future__ import annotations

from nora.domain.ports import speakable


def test_clean_cantonese_is_speakable():
    assert speakable("喂，我係 Nora。今日天氣幾好，出街記得帶遮。") is True


def test_a_short_english_sentence_is_speakable():
    assert speakable("Dinner is at seven, see you there.") is True


def test_a_url_is_not_speakable():
    assert speakable("睇呢個 https://example.com/articles/1234") is False
    assert speakable("http://example.com 有詳情") is False


def test_a_bare_hostname_is_not_speakable():
    assert speakable("去 github.com 睇吓") is False
    assert speakable("www.example.com 上面寫咗") is False


def test_a_handle_is_not_speakable():
    assert speakable("@miles 你睇吓呢個") is False


def test_a_long_hex_run_is_not_speakable():
    assert speakable("個 hash 係 a3f2c1d4e5b60718293a4b5c") is False


def test_a_uuid_is_not_speakable():
    assert speakable("id 係 123e4567-e89b-12d3-a456-426614174000") is False


def test_a_long_base64_token_is_not_speakable():
    assert speakable("key 係 ghp9A1b2C3d4E5f6G7h8I9j0K1l2M3n4") is False


def test_a_short_numeric_id_stays_speakable():
    """「ID 係 1234」 is fine — rule 2 draws the line at LONG opaque strings,
    and a gate that rejects a four-digit number would silence ordinary
    answers, the false-negative direction the rule warns about."""
    assert speakable("ID 係 1234，記低佢。") is True


def test_an_ordinary_long_word_is_not_misread_as_a_token():
    """The base64 pattern demands a digit or a base64-only character in the
    run, so a long WORD stays speakable — documented so a future tightening
    of the regex has to say why this changed."""
    assert speakable("antidisestablishmentarianism is a long word") is True
