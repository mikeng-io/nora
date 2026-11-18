"""Extract authored text and discard private model reasoning."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_PRIVATE = frozenset({
    "reasoning", "reasoningContent", "reasoning_content", "thinking", "redactedThinking",
    "redacted_thinking", "reasoningText", "reasoning_text", "analysis", "hidden_reasoning",
})


def authored_text(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (tuple, list)):
        return [text for item in raw for text in authored_text(item)]
    if not isinstance(raw, Mapping) or raw.get("role", "assistant") != "assistant":
        return []
    if raw.get("type") in _PRIVATE:
        return []
    if "content" in raw:
        return authored_text(raw["content"])
    if isinstance(raw.get("text"), str):
        return authored_text(raw["text"])
    return []


def without_reasoning(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("type") in _PRIVATE:
            return None
        return {key: without_reasoning(item) for key, item in value.items() if key not in _PRIVATE}
    if isinstance(value, (list, tuple)):
        return [without_reasoning(item) for item in value
                if not isinstance(item, Mapping) or item.get("type") not in _PRIVATE]
    return value
