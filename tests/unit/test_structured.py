"""`nora.agent.structured.ask_text`'s `MaxTokensReachedException` catch.

Strands' event loop raises this unconditionally on a max-tokens cutoff
(`event_loop.py:304-311`) rather than returning normally, so
`nora.engagement.stages.debrief._check`'s `finish_reason == "length"` branch
can only ever fire if `ask_text` converts the raise itself — the same salvage
`nora.agent.factory.invoke` already does for the tag turn's Agent invocation
(`tests/test_factory.py`'s two tests of the same shape).
"""

from __future__ import annotations

from typing import Any

from strands.types.exceptions import MaxTokensReachedException

from nora.agent import structured


class _FakeAgent:
    def __init__(self, **_: Any) -> None:
        self.messages: list[dict[str, Any]] = [
            {"role": "assistant", "content": [{"text": "呢個係部分回覆"}]}
        ]

    async def invoke_async(self, *args: Any, **kwargs: Any) -> Any:
        raise MaxTokensReachedException("max tokens")


class _ToolOnlyAgent(_FakeAgent):
    def __init__(self, **_: Any) -> None:
        self.messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "text": "The selected tool send_reply's tool use "
                        "was incomplete due to maximum token limits "
                        "being reached."
                    }
                ],
            }
        ]


async def test_ask_text_catches_max_tokens_and_carries_the_partial(monkeypatch):
    monkeypatch.setattr(structured, "Agent", _FakeAgent)
    result = await structured.ask_text(
        model=object(),  # type: ignore[arg-type]
        prompt="p",
        timeout_seconds=5.0,
    )
    assert result.finish_reason == "length"
    assert result.content == "呢個係部分回覆"
    assert result.usage is None


async def test_ask_text_carries_an_empty_partial_when_only_tool_use_blocks(monkeypatch):
    monkeypatch.setattr(structured, "Agent", _ToolOnlyAgent)
    result = await structured.ask_text(
        model=object(),  # type: ignore[arg-type]
        prompt="p",
        timeout_seconds=5.0,
    )
    assert result.finish_reason == "length"
    assert result.content == ""


class _Metrics:
    def __init__(self, usage: dict[str, int]) -> None:
        self.accumulated_usage = usage


class _Result:
    def __init__(self, *, text: str, stop_reason: str, usage: dict[str, int]) -> None:
        self._text = text
        self.stop_reason = stop_reason
        self.metrics = _Metrics(usage)

    def __str__(self) -> str:
        return self._text


class _NormalAgent:
    """The un-exceptional path: `ask_text`'s normal return, which the `MaxTokensReachedException`
    tests do not exercise."""

    def __init__(self, **_: Any) -> None:
        pass

    async def invoke_async(self, *args: Any, **kwargs: Any) -> Any:
        return _Result(
            text=" 好呀，等我幫你搵 ",
            stop_reason="stop",
            usage={"inputTokens": 12, "outputTokens": 34, "totalTokens": 46},
        )


async def test_ask_text_normal_completion_strips_content_and_carries_usage(monkeypatch):
    monkeypatch.setattr(structured, "Agent", _NormalAgent)
    result = await structured.ask_text(
        model=object(),  # type: ignore[arg-type]
        prompt="p",
        timeout_seconds=5.0,
    )
    assert result.content == "好呀，等我幫你搵"
    assert result.finish_reason == "stop"
    assert result.usage == (12, 34, 46)


class _MaxTokensStopReasonAgent(_NormalAgent):
    """Strands' event loop raises on `stop_reason == "max_tokens"`
    unconditionally (`event_loop.py:304-311`) — a normal RETURN carrying that
    stop_reason cannot happen against the real SDK. This fake reproduces it
    anyway, because `ask_text`'s own translation line
    (`"max_tokens" -> "length"`) is dead code against the real SDK for
    exactly that reason, and the only way to pin it is a fake that lies
    about which path it took."""

    async def invoke_async(self, *args: Any, **kwargs: Any) -> Any:
        return _Result(text="cut off", stop_reason="max_tokens", usage={})


async def test_ask_text_translates_a_normally_returned_max_tokens_stop(monkeypatch):
    monkeypatch.setattr(structured, "Agent", _MaxTokensStopReasonAgent)
    result = await structured.ask_text(
        model=object(),  # type: ignore[arg-type]
        prompt="p",
        timeout_seconds=5.0,
    )
    assert result.finish_reason == "length"
