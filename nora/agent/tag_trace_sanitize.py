"""Strip synthetic (non-Nora-authored) artifacts from `agent.messages` before
they fold into a `TagTrace`, and cap the result to a token-ish character
budget -- truncating assistant narration, never a human's own words (intent
cannot be reconstructed from the work that followed).

Three synthetic shapes (Strands 1.50.2: `strands/event_loop/event_loop.py`,
`_recover_message_on_max_tokens_reached.py`), listed in `_GUARD_VETO_STRINGS`
and the two constant-text matches below. A future SDK bump may change these;
this module degrades to "nothing stripped" rather than raising if a shape is
absent, per `nora.agent.tag_trace_extract`'s defensive-throughout rule.
"""

from __future__ import annotations

import re

# ReplyGuard's own veto strings that can reach agent.messages as a synthesised
# assistant turn when BeforeModelCallEvent.cancel fires. Not imported from
# nora.agent.guards to avoid a runtime dependency on that module's internal
# string literals; kept here as the sanitizer's own contract, pinned by the
# test above quoting one verbatim.
_GUARD_VETO_STRINGS = frozenset({
    "Reply already sent — the turn is finished.",
    "model call denied by hook",
})

_CANCELLED_TOOL_RESULT = [{"text": "Tool execution cancelled"}]

_MAX_TOKENS_PLACEHOLDER_RE = re.compile(
    r"^The selected tool .+'s tool use was incomplete due to maximum token "
    r"limits being reached\.$"
)


def _is_synthetic_text_block(block: dict) -> bool:
    text = block.get("text")
    if text in _GUARD_VETO_STRINGS:
        return True
    return isinstance(text, str) and bool(_MAX_TOKENS_PLACEHOLDER_RE.match(text))


def _is_synthetic_tool_result_block(block: dict) -> bool:
    result = block.get("toolResult")
    if not isinstance(result, dict):
        return False
    return result.get("content") == _CANCELLED_TOOL_RESULT


def strip_synthetic_artifacts(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for message in messages:
        content = message.get("content") or []
        kept = [
            block for block in content
            if not _is_synthetic_text_block(block)
            and not _is_synthetic_tool_result_block(block)
        ]
        if kept:
            out.append({**message, "content": kept})
    return out


def cap_messages(
    messages: list[dict], *, max_chars: int,
) -> tuple[list[dict], int]:
    """Truncate assistant text blocks over `max_chars`; leave user (human)
    turns untouched regardless of length. Returns `(capped, truncated_count)`
    -- the count feeds `nora_tagtrace_truncations_total`, never silent."""
    out: list[dict] = []
    truncated = 0
    for message in messages:
        if message.get("role") != "assistant":
            out.append(message)
            continue
        new_content = []
        message_truncated = False
        for block in message.get("content") or []:
            text = block.get("text")
            if isinstance(text, str) and len(text) > max_chars:
                new_content.append({**block, "text": text[:max_chars]})
                message_truncated = True
            else:
                new_content.append(block)
        if message_truncated:
            truncated += 1
        out.append({**message, "content": new_content})
    return out, truncated
