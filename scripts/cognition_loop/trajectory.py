"""One wake's full trajectory, sliced out of the run's evidence events.

Production serialises each session event's fields inside the JSON
`attributes.body` — `nora.session.started` (system prompt, agenda,
items, stimuli), `nora.session.instructions` (doctrine and skill
versions), `nora.session.terminal` (outcome, error, bound) — while a
`nora.model.request` carries `nora.model.request.body`. A trajectory is
bound to its session: the window opens at the `started` whose body
names this session and wake, and closes at its `terminal`. A wake with
no session produces an empty trajectory — never the previous wake's.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.memory_graph_spike import evidence_exchanges


def result_values(result: Any) -> list[Any]:
    """A Strands tool result's content values, JSON-decoded — the same
    decode `evidence_exchanges` applies, so a caller never reads the
    raw envelope."""
    if not isinstance(result, dict):
        return []
    values = []
    for part in result.get("content") or []:
        if not isinstance(part, dict):
            continue
        if "json" in part:
            values.append(part["json"])
        elif "text" in part:
            try:
                values.append(json.loads(part["text"]))
            except ValueError:
                values.append(part["text"])
    return values


def tool_outcome(result: Any) -> dict[str, Any]:
    """The tool's own three-state outcome inside the Strands envelope: the
    envelope says `success` for a refused or unavailable write too."""
    for value in result_values(result):
        if isinstance(value, dict) and "status" in value:
            return value
    return {}


def _body(event: dict[str, Any]) -> dict[str, Any]:
    attrs = event.get("attributes") or {}
    raw = attrs.get("body") or attrs.get("nora.model.request.body")
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def session_events(
    events: list[dict[str, Any]], *, session_id: str | None,
    wake_id: int | None = None,
) -> list[dict[str, Any]]:
    """The events from the `started` naming `session_id`/`wake_id` to its
    `terminal`, inclusive. No matching session: empty."""
    start = None
    for i, event in enumerate(events):
        if event.get("name") != "nora.session.started":
            continue
        body = _body(event)
        if body.get("session_id") == session_id and (
            wake_id is None or body.get("wake_id") == wake_id
        ):
            start = i
    if start is None:
        return []
    out: list[dict[str, Any]] = []
    for event in events[start:]:
        out.append(event)
        if (event.get("name") == "nora.session.terminal"
                and _body(event).get("session_id") == session_id):
            break
    return out


def wake_trajectory(
    events: list[dict[str, Any]], *, wake_id: int,
    session_id: str | None, tokens: int | None,
) -> dict[str, Any]:
    """The session's events for one wake, grouped for a judge to read."""
    started: dict[str, Any] = {}
    instructions: dict[str, Any] = {}
    terminal: dict[str, Any] = {}
    model: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for entry in events:
        name = entry.get("name")
        if name == "nora.session.started":
            started = _body(entry)
        elif name == "nora.session.instructions":
            instructions = _body(entry)
        elif name == "nora.session.terminal":
            terminal = _body(entry)
        elif name == "nora.model.request":
            model.append(_body(entry))
        elif entry.get("kind") == "tool_finished":
            use = entry.get("tool") or {}
            outcome = tool_outcome(entry.get("result"))
            tools.append({
                "tool": use.get("name"),
                "arguments": use.get("input"),
                "status": outcome.get("status"),
                "reason": outcome.get("reason"),
                "result": entry.get("result"),
                "error_type": entry.get("error_type"),
                "refusal": entry.get("argument_validation"),
            })
    return {
        "wake_id": wake_id,
        "session_id": session_id,
        "blocks": {
            "system": started.get("system"),
            "agenda": started.get("agenda"),
            "items": started.get("items"),
            "stimuli": started.get("stimuli"),
            "instructions": instructions or None,
        },
        "model_calls": model,
        "exchanges": evidence_exchanges(events),
        "tool_calls": tools,
        "stop_reason": terminal.get("outcome") or terminal.get("stop_reason"),
        "usage": {"tokens": tokens},
    }
