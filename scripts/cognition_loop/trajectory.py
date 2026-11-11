"""One wake's full trajectory, sliced out of the run's evidence events.

The subject's session records `nora.session.started` (the assembled
system prompt, agenda, items, stimuli and instruction text), every
`nora.model.request`/`transport_response` (request body, response,
usage, reasoning availability), every `tool_started`/`tool_finished`
(arguments, result, refusal), and `nora.session.terminal` (stop
reason). The trajectory serialises those events per wake — the verdict's
evidence — without re-deriving them from logs.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.memory_graph_spike import evidence_exchanges

SESSION_EVENT_NAMES = {
    "nora.session.started", "nora.model.request", "nora.session.terminal",
}


def session_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events of the last session on the record."""
    starts = [
        i for i, e in enumerate(events) if e.get("name") == "nora.session.started"
    ]
    return events[starts[-1]:] if starts else []


def wake_trajectory(
    events: list[dict[str, Any]], *, wake_id: int,
    session_id: str | None, tokens: int | None,
) -> dict[str, Any]:
    """The session's events for one wake, grouped for a judge to read."""
    started = _last(events, "nora.session.started")
    terminal = _last(events, "nora.session.terminal")
    model: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for entry in events:
        name = entry.get("name")
        attrs = entry.get("attributes") or {}
        if name == "nora.model.request":
            model.append(json.loads(attrs["nora.model.request.body"]))
        elif entry.get("kind") == "tool_finished":
            use = entry.get("tool") or {}
            tools.append({
                "tool": use.get("name"),
                "arguments": use.get("input"),
                "result": entry.get("result"),
                "error_type": entry.get("error_type"),
                "refusal": entry.get("argument_validation"),
            })
    return {
        "wake_id": wake_id,
        "session_id": session_id,
        "blocks": {
            "system": _attr(started, "system"),
            "agenda": _attr(started, "agenda"),
            "items": _attr(started, "items"),
            "stimuli": _attr(started, "stimuli"),
            "instructions": _attr(started, "instructions"),
        },
        "model_calls": model,
        "exchanges": evidence_exchanges(events),
        "tool_calls": tools,
        "stop_reason": _attr(terminal, "stop_reason") or _attr(terminal, "outcome"),
        "usage": {"tokens": tokens},
    }


def _last(events: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    found = None
    for entry in events:
        if entry.get("name") == name:
            found = entry
    return found


def _attr(entry: dict[str, Any] | None, key: str) -> Any:
    if entry is None:
        return None
    return (entry.get("attributes") or {}).get(key)
