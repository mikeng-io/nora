"""`[speech.tts]`'s knobs are refused at load, not by the vendor mid-turn."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nora.config import SpeechTtsConfig, SpeechTtsFormatConfig


def test_the_formatter_ships_off(example_config):
    assert example_config.speech.tts.format.mode == "off"
    assert SpeechTtsConfig().format.mode == "off"
    assert SpeechTtsConfig().pronunciation == []
    assert SpeechTtsConfig().text_normalization is False


def test_whisper_is_refused_because_speech_2_8_does_not_support_it():
    """Per the vendor's documentation, speech-2.8 models do not support
    `whisper`. Accepting it would fail every synthesis and send every spoken
    reply as text."""
    with pytest.raises(ValidationError):
        SpeechTtsConfig(emotion="whisper")  # type: ignore[arg-type]


@pytest.mark.parametrize("emotion", ["", "fluent", "happy", "calm"])
def test_the_emotions_the_model_takes_are_accepted(emotion):
    assert SpeechTtsConfig(emotion=emotion).emotion == emotion  # type: ignore[arg-type]


@pytest.mark.parametrize("entry", ["RV", "RV/", "/(aa1)", "a/b/c", " /x"])
def test_a_pronunciation_entry_must_be_one_pair(entry):
    with pytest.raises(ValidationError):
        SpeechTtsConfig(pronunciation=[entry])


def test_a_well_formed_pronunciation_entry_is_kept():
    assert SpeechTtsConfig(pronunciation=["RV/(aa1)(wai1)"]).pronunciation == ["RV/(aa1)(wai1)"]


def test_min_chars_above_max_chars_is_refused():
    with pytest.raises(ValidationError):
        SpeechTtsFormatConfig(min_chars=50, max_chars=10)


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValidationError):
        SpeechTtsFormatConfig(mode="always")  # type: ignore[arg-type]
