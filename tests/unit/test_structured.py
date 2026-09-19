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
