"""Her reasoning reaches her next call: `ReasoningCarryingOpenAIModel`.

Issue #325, ADR 0013. Strands' `OpenAIModel._format_regular_messages` drops
every `reasoningContent` block out of a Chat Completions request, so in a
tool-using loop each continuation call was formed WITHOUT the reasoning the
previous call produced. `reasoning_strip_log` measured it on production
(2026-09-17 to 2026-09-24): every continuation call on the two Dreamer paths
that run at `reasoning_effort = "low"` (`dreamer:proactive`, `dreamer:culture`)
was a strip, 86 of 86, over 46 samples; the paths at `none` produced nothing to
strip.

**What the upstream reads, measured rather than assumed** (2026-09-25, direct
requests against OmniRoute on the deploy host, aliases `nora-dreamer` and
`nora`, both served by `deepseek-v4.1-flash`, one multi-turn tool exchange,
the same second request with one field added to the prior assistant message
carrying ~1,200 tokens of padding; `prompt_tokens` is the reading):

    field on the assistant message        prompt_tokens   reaches the model
    (none: the shape Strands sends)            597             -
    reasoning_content                          597             NO, discarded
    thinking                                   597             NO, discarded
    reasoning                                 1798             YES
    content (control)                         1797             YES

Streaming requests read the same (481 / 481 / 1682). The response carries the
model's reasoning as `reasoning`, not `reasoning_content`, and no variant was
refused (every arm 200), including the one Strands sends today: the upstream
neither REQUIRES the reasoning back nor rejects it. It simply never saw it.

So the field is `reasoning`, the spelling the upstream both writes and reads.
`reasoning_content` (DeepSeek's native API) would have been accepted and
silently thrown away, which is exactly the defect a fix must not recreate: a
request that looks carried and is not.

**Scope.** Only assistant messages, and only the ones the formatter will emit
(a text or tool-call block beside the reasoning): an assistant message whose
only content is reasoning is dropped whole by `format_request_messages`, and
its reasoning with it. `uncarried_reasoning_blocks` counts exactly what is
still lost, and `nora.agent.reasoning_strip` asks it instead of assuming every
block is. Every reasoning block in `agent.messages` comes from THIS loop
(`nora.agent.prefill` renders plain text, ADR 0013 §3), so carrying all of them
is carrying the current turn's reasoning, never a past turn's.

Dash-free is kept and applies to the carried text too: the reasoning is
something she READS on the next call, which is the line `nora.agent.
punctuation` draws.
"""

from __future__ import annotations

from typing import Any

from .punctuation import DashFreeOpenAIModel
from .reasoning_strip import REASONING_BLOCK_KEY

#: The request field the upstream reads a prior assistant message's reasoning
#: from. Measured, see the module docstring: `reasoning_content` is discarded.
CARRIED_REASONING_FIELD = "reasoning"

#: Content blocks that make the formatter EMIT an assistant message: text
#: becomes `content`, a tool use becomes `tool_calls`. A message with neither
#: is filtered out of the request, so reasoning attached to it goes nowhere.
_EMITTED_BLOCK_KEYS = ("text", "toolUse")


def _reasoning_text(block: Any) -> str:
    """The readable text of one `reasoningContent` block; `""` for a redacted
    or unreadable one, which cannot be carried as text."""
    content = block.get(REASONING_BLOCK_KEY) if isinstance(block, dict) else None
    reasoning_text = content.get("reasoningText") if isinstance(content, dict) else None
    text = reasoning_text.get("text") if isinstance(reasoning_text, dict) else None
    return text if isinstance(text, str) else ""


def _carries(message: Any) -> bool:
    """Whether this message's reasoning is carried into the request."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and any(key in block for key in _EMITTED_BLOCK_KEYS)
        for block in content
    )


def carried_reasoning(message: Any) -> str:
    """The reasoning text this message carries into the request, or `""`."""
    if not _carries(message):
        return ""
    return "\n\n".join(
        text for text in (_reasoning_text(block) for block in message["content"]) if text
    )


def uncarried_reasoning_blocks(messages: Any) -> int:
    """How many `reasoningContent` blocks in this message list the request
    built by `ReasoningCarryingOpenAIModel` will still DROP.

    The same walk as `reasoning_strip.reasoning_blocks`, minus the blocks
    `carried_reasoning` puts on the wire. Defensive at every level for that
    function's reason: it runs inside a hook on a third-party structure.
    """
    total = 0
    for message in messages or ():
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        carried = _carries(message)
        total += sum(
            1 for block in content
            if isinstance(block, dict) and REASONING_BLOCK_KEY in block
            and not (carried and _reasoning_text(block))
        )
    return total


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

    def uncarried_reasoning_blocks(self, messages: Any) -> int:
        """What `nora.agent.reasoning_strip.ReasoningStripWatch` counts as a
        strip for a call made through this model."""
        return uncarried_reasoning_blocks(messages)
