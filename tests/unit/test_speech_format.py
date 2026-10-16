"""`nora.agent.speech_format.SpeechFormatter`: the model's pacing, or her
words as written.

The fake model answers the way a cheap one does, including wrong: it rewrites
a word, invents a tag, stacks pauses, wraps the answer in prose, stalls, or
fails. Every one of those must come back as her normalised reply, be counted,
and never raise. Fixtures are invented Cantonese, never a recorded reply.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from nora.agent.speech_format import SpeechFormatter, render_input
from nora.domain.speech_markup import FORMAT_OUTCOMES
from nora.obs.metrics import Metrics

_REPLY = "好呀😂 聽日三點喺旺角等，記得帶把遮"
_BASE = "好呀 聽日三點喺旺角等，記得帶把遮"


@dataclass
class _Said:
    content: str
    finish_reason: str = "stop"


class _Model:
    def __init__(self, answer: str = "", *, finish: str = "stop",
                 stall: bool = False, boom: bool = False) -> None:
        self.answer, self.finish = answer, finish
        self.stall, self.boom = stall, boom
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, prompt: str) -> _Said:
        self.calls.append((system, prompt))
        if self.boom:
            raise RuntimeError("provider 502")
        if self.stall:
            await asyncio.sleep(10)
        return _Said(self.answer, self.finish)


class _Prompts:
    def __init__(self, text: str = "# What you are reading\nPace it.") -> None:
        self.text = text

    def get(self, *, applies_to: str = "speech_format"):
        assert applies_to == "speech_format"
        return SimpleNamespace(doctrine_text=self.text)


def _formatter(model: _Model, metrics: Metrics, *, prompts: _Prompts | None = None,
               min_chars: int = 4, max_chars: int = 200) -> SpeechFormatter:
    return SpeechFormatter(
        client=model, prompts=prompts or _Prompts(), metrics=metrics,
        timeout_seconds=0.2, min_chars=min_chars, max_chars=max_chars,
    )


def _count(metrics: Metrics, outcome: str) -> float:
    return metrics.get("nora_speech_format_total", outcome=outcome)


async def test_a_paced_reply_that_keeps_every_word_is_spoken():
    metrics = Metrics()
    paced = "好呀(laughs) 聽日三點喺旺角等，<#0.30#>記得帶把遮"
    model = _Model(paced)

    assert await _formatter(model, metrics).format(text=_REPLY) == paced
    assert _count(metrics, "formatted") == 1
    # It read her NORMALISED reply, in its one described block, and nothing
    # else: no emoji reached it.
    ((system, prompt),) = model.calls
    assert prompt == render_input(_BASE)
    assert "😂" not in prompt and "<spoken_reply" in prompt
    assert system.startswith("# What you are reading")
    assert len(metrics.observations("nora_speech_format_seconds")) == 1


@pytest.mark.parametrize(("answer", "outcome"), [
    # The model rewrote a word (三 -> 四).
    ("好呀 聽日四點喺旺角等，記得帶把遮", "words_changed"),
    # It invented an interjection MiniMax does not perform.
    ("好呀(giggles) 聽日三點喺旺角等，記得帶把遮", "words_changed"),
    # It stacked two pauses.
    ("好呀 <#0.5#><#0.5#>聽日三點喺旺角等，記得帶把遮", "bad_markup"),
    # It explained itself.
    ("Sure: 好呀 聽日三點喺旺角等，記得帶把遮", "words_changed"),
    # It answered nothing.
    ("", "empty"),
], ids=["rewrote-a-word", "invented-tag", "consecutive-pauses", "prose", "empty"])
async def test_a_refused_answer_speaks_her_reply_normalised(answer, outcome):
    metrics = Metrics()
    got = await _formatter(_Model(answer), metrics).format(text=_REPLY)
    assert got == _BASE
    assert _count(metrics, outcome) == 1
    assert _count(metrics, "formatted") == 0


async def test_an_answer_with_no_markup_speaks_her_reply_not_the_models():
    """Punctuation the model moved is not worth speaking its copy for."""
    metrics = Metrics()
    model = _Model("好呀，聽日三點喺旺角等。記得帶把遮")
    got = await _formatter(model, metrics).format(text=_REPLY)
    assert got == _BASE
    assert _count(metrics, "unchanged") == 1


async def test_a_stalled_model_is_cut_at_the_timeout_and_her_reply_is_spoken():
    metrics = Metrics()
    got = await asyncio.wait_for(
        _formatter(_Model(stall=True), metrics).format(text=_REPLY), timeout=2,
    )
    assert got == _BASE
    assert _count(metrics, "timeout") == 1
    (seconds,) = metrics.observations("nora_speech_format_seconds")
    assert seconds < 1.0


async def test_a_failing_model_never_raises():
    metrics = Metrics()
    assert await _formatter(_Model(boom=True), metrics).format(text=_REPLY) == _BASE
    assert _count(metrics, "error") == 1


async def test_a_truncated_answer_is_refused_even_if_it_would_pass():
    metrics = Metrics()
    got = await _formatter(_Model(_BASE, finish="length"), metrics).format(text=_REPLY)
    assert got == _BASE
    assert _count(metrics, "truncated") == 1


async def test_no_instruction_means_no_call():
    metrics = Metrics()
    model = _Model("unused")
    got = await _formatter(model, metrics, prompts=_Prompts("  ")).format(text=_REPLY)
    assert got == _BASE and model.calls == []
    assert _count(metrics, "no_instruction") == 1


async def test_a_short_reply_skips_the_model():
    metrics = Metrics()
    model = _Model("unused")
    assert await _formatter(model, metrics, min_chars=12).format(text="好呀👍") == "好呀"
    assert model.calls == []
    assert _count(metrics, "short") == 1
    assert metrics.observations("nora_speech_format_seconds") == []


async def test_a_long_reply_skips_the_model():
    metrics = Metrics()
    model = _Model("unused")
    assert await _formatter(model, metrics, max_chars=5).format(text=_REPLY) == _BASE
    assert model.calls == []
    assert _count(metrics, "long") == 1


def test_every_outcome_the_formatter_counts_is_declared():
    """The label vocabulary is the Literal the help text is built from; every
    outcome the tests above drive must be in it."""
    for outcome in ("formatted", "unchanged", "short", "long", "words_changed",
                    "bad_markup", "empty", "truncated", "timeout", "error",
                    "no_instruction"):
        assert outcome in FORMAT_OUTCOMES
