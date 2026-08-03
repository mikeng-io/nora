import pytest

from nora.tag import is_tagged

TRIGGER = "nora.000"

MATRIX = [
    # (body, fires, why)
    ("nora.000 what time", True, "plain trigger"),
    ("NORA.000", True, "case-insensitive"),
    ("hey nora.000!", True, "punctuation boundary"),
    ("nora.000，你在嗎", True, "CJK punctuation boundary"),
    ("nora.0001", False, "trailing word char"),
    ("xnora.000", False, "leading word char"),
    ("nora_520", False, "not the trigger"),
    ("nora .520", False, "space inside the token"),
    ("nor a.520", False, "space inside the token"),
    ("`nora.000`", False, "inline code span"),
    ("```\nnora.000\n```", False, "fenced code block"),
    ("look: ```nora.000``` in code", False, "inline triple-backtick span"),
    ("n​ora.520", True, "zero-width space stripped"),
    ("nora​.520", True, "zero-width space stripped"),
    ("‮nora.000", True, "bidi control stripped"),
    ("> nora.000", True, "a quote is still the sender's own body"),
    ("", False, "empty body"),
    ("just talking about nora", False, "name without the token"),
    ("nora.000 and nora.000 again", True, "multiple occurrences still fire once"),
    ("ask nora.000﻿ now", True, "BOM stripped"),
]


@pytest.mark.parametrize("body,fires,why", MATRIX, ids=[m[2] for m in MATRIX])
def test_trigger_matrix(body, fires, why):
    assert is_tagged(body, TRIGGER) is fires, why


def test_trigger_is_configurable_without_a_code_change():
    assert is_tagged("hey @bot!", "@bot") is True
    assert is_tagged("hey @bot!", "nora.000") is False


def test_regex_metacharacters_in_the_trigger_are_escaped():
    assert is_tagged("call n.ra now", "n.ra") is True
    assert is_tagged("call nXra now", "n.ra") is False


def test_code_span_stripping_leaves_surrounding_text_taggable():
    assert is_tagged("`code` nora.000 real", TRIGGER) is True


def test_unclosed_fence_swallows_the_rest():
    """An unterminated fence is treated as code to the end — a conservative miss."""
    assert is_tagged("```\nnora.000", TRIGGER) is False
