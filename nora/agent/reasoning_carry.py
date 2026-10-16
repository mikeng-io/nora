"""Her reasoning reaches her next call: `ReasoningCarryingOpenAIModel`.

Strands' `OpenAIModel._format_regular_messages` drops every
`reasoningContent` block out of a Chat Completions request, so without this a
continuation call in a tool-using loop is formed WITHOUT the reasoning the
previous call produced (ADR 0013).

**Which field.** The upstream (OmniRoute) writes the model's reasoning as
`reasoning` and reads it back as `reasoning`. `reasoning_content` (DeepSeek's
native API) and `thinking` are accepted without error and silently discarded,
which is the defect this class exists to avoid: a request that looks carried
and is not. The upstream neither REQUIRES the reasoning back nor rejects it,
so the field must be the one it reads.

**Scope.** Assistant messages only. One whose only content is reasoning is
still dropped whole by the SDK's `format_request_messages` (it keeps only
messages with `content` or `tool_calls`), and its reasoning with it.
`nora.agent.reasoning_strip.dropped_reasoning_blocks` judges what is still
lost against the request this class formats, never against a prediction of
it. Every reasoning block in `agent.messages` comes from THIS loop
(`nora.agent.prefill` renders plain text, ADR 0013 §3), so carrying all of
them is carrying the current turn's reasoning, never a past turn's.

Dash-free applies to the carried text too: the reasoning is something she
READS on the next call, which is the line `nora.agent.punctuation` draws.
"""

from __future__ import annotations

from typing import Any

from .punctuation import DashFreeOpenAIModel
from .reasoning_strip import (
    CARRIED_REASONING_FIELD,
    REASONING_BLOCK_KEY,
    reasoning_text,
)


def carried_reasoning(message: Any) -> str:
    """The reasoning text an ASSISTANT message carries into the request, or
    `""`. Every assistant message gets it; one with nothing else to emit is
    filtered out of the request whole by the SDK, reasoning included, and the
    detector reads that off the formatted request rather than assuming it."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    if not isinstance(message.get("content"), list):
        return ""
    return "\n\n".join(
        text for text in (reasoning_text(block) for block in message["content"]) if text
    )


class ReasoningCarryingOpenAIModel(DashFreeOpenAIModel):
    """`DashFreeOpenAIModel` whose request carries each assistant message's
    reasoning as `reasoning`, instead of dropping it."""

    @classmethod
    def _format_regular_messages(cls, messages: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """The SDK's formatter, one message at a time, with the reasoning
        blocks taken out before it sees them and put back as a field after.

        One message at a time is the SDK's own structure: its loop formats each
        message independently and only appends, so formatting a one-message
        list per message yields the same request. Taking the blocks out first
        also means the SDK's «not supported in multi-turn» WARNING no longer
        fires for a block that IS carried.
        """
        formatted: list[dict[str, Any]] = []
        for message in messages:
            content = message["content"]
            reasoning = carried_reasoning(message)
            if reasoning:
                message = {
                    **message,
                    "content": [b for b in content if REASONING_BLOCK_KEY not in b],
                }
            part = super()._format_regular_messages([message], **kwargs)
            if reasoning and part:
                # `part[0]` is this message; any further entries are the tool
                # messages the SDK splits out after it.
                part[0][CARRIED_REASONING_FIELD] = reasoning
            formatted.extend(part)
        return formatted
