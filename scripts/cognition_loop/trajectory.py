"""One wake's full trajectory, sliced out of the run's evidence events.

Production serialises each session event's fields inside the JSON
`attributes.body` — `nora.session.started` (system prompt, agenda,
items, stimuli), `nora.session.instructions` (doctrine and skill
versions), `nora.session.terminal` (outcome, error, bound) — while a
`nora.model.request` carries `nora.model.request.body`. A trajectory is
bound to its session: the window opens at the `started` whose body
names this session and wake, and closes at its committed `settled`. A wake with
no session produces an empty trajectory — never the previous wake's.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nora.subject.effects import tool_outcome
from scripts.memory_graph_spike import evidence_exchanges

_MEMORY_READS = ("memory_search", "memory_read")
#: The prefetch block's row shapes (`preview_text`): `@kind:id` node rows and
#: `rid: src ─rel─▶ dst` relation rows — a relation row shows BOTH endpoints,
#: so a node row the budget omitted is still a shown key.
_GRAPH_BLOCK = re.compile(r"<graph\b[^>]*>\n(.*?)\n</graph>", re.DOTALL)
_PREFETCH_KEY = re.compile(r"^@(\w+):(\S+)", re.MULTILINE)
_PREFETCH_REL = re.compile(r"^(\S+): (\w+):(\S+) ─\w+─▶ (\w+):(\S+)", re.MULTILINE)


def prefetch_refs(agenda: Any) -> tuple[set[tuple[str, str]], set[str]] | None:
    """The keys and relation ids the wake's recorded prefetch emitted —
    the `<graph>` block's emitted rows, relation rows contributing both
    endpoints. `None` when the trajectory recorded no prefetch block: a
    count that reads it is unavailable, never zero."""
    if not isinstance(agenda, str):
        return None
    block = _GRAPH_BLOCK.search(agenda)
    if block is None:
        return None
    text = block.group(1)
    keys = {(m.group(1), m.group(2)) for m in _PREFETCH_KEY.finditer(text)}
    relations: set[str] = set()
    for m in _PREFETCH_REL.finditer(text):
        relations.add(m.group(1))
        keys |= {(m.group(2), m.group(3)), (m.group(4), m.group(5))}
    return keys, relations


def _prefetch(
    trajectory: dict[str, Any],
) -> tuple[set[tuple[str, str]], set[str]] | None:
    """The session's recorded `prefetch` provenance, else the block's rows."""
    recorded = trajectory.get("prefetch")
    if isinstance(recorded, dict):
        keys = {
            tuple(k) for k in recorded.get("keys") or ()
            if isinstance(k, (list, tuple)) and len(k) == 2
        }
        relations = {
            r for r in recorded.get("relations") or () if isinstance(r, str)
        }
        return keys, relations
    return prefetch_refs((trajectory.get("blocks") or {}).get("agenda"))


def loop_counts(
    trajectory: dict[str, Any], diff: Any, prior_links: set[str],
) -> dict[str, Any]:
    """The wake's loop over the cognitive graph as counts: keys discovered
    (distinct keys memory reads returned), of those how many were already
    shown earlier in the session (prefetch included), graph links the diff
    wrote or revised, and how many of the previous wake's links this wake's
    recorded prefetch carried. What the trajectory cannot ground is None —
    unavailable — never zero."""
    reads = [
        c for c in trajectory.get("tool_calls") or ()
        if c.get("tool") in _MEMORY_READS and c.get("status") in ("ok", "partial")
    ]
    prefetch = _prefetch(trajectory)
    discovered: set[tuple[str, str]] = set()
    repeated: set[tuple[str, str]] = set()
    earlier = set(prefetch[0]) if prefetch is not None else set()
    for call in reads:
        outcome = tool_outcome(call.get("result"))
        keys = {
            tuple(k) for k in outcome.get("keys") or ()
            if isinstance(k, (list, tuple)) and len(k) == 2
        }
        repeated |= keys & earlier
        discovered |= keys
        earlier |= keys
    graph_diff = diff.kinds.get("graph") if diff is not None else None
    written = (
        {e.after["id"] for e in (*graph_diff.created, *graph_diff.revised)
         if e.after is not None and "id" in e.after}
        if graph_diff is not None else set()
    )
    return {
        "discovered": len(discovered),
        "repeats": len(discovered & repeated) if (prefetch is not None or not reads) else None,
        "links_written": len(written),
        "links_carried": (
            len(prior_links & prefetch[1]) if prefetch is not None else None
        ),
        "link_ids": sorted(written),
    }


def _delivery_invocations(events: list[dict[str, Any]]) -> set[str]:
    """Delivery invocations, excluding downstream tool-less folds in the same trace."""
    owners: set[str] = set()
    for event in events:
        if event.get("name") != "nora.model.request":
            continue
        names = {
            name for tool in _body(event).get("tools") or []
            if isinstance(tool, dict)
            for name in (
                tool.get("name"),
                (tool.get("function") or {}).get("name")
                if isinstance(tool.get("function"), dict) else None,
                (tool.get("toolSpec") or {}).get("name")
                if isinstance(tool.get("toolSpec"), dict) else None)
            if isinstance(name, str)}
        if "send_reply" in names and event.get("invocation_id"):
            owners.add(event["invocation_id"])
    return owners


def terminal_response(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Select a settled terminal attempt; never infer output from a request."""
    starts = [(i, e) for i, e in enumerate(events)
              if e.get("kind") == "invocation_started"]
    ids = {e.get("invocation_id") for _, e in starts}
    roots = [(i, e) for i, e in starts if e.get("parent_invocation_id") not in ids]
    if len(roots) != 1:
        owners = _delivery_invocations(events)
        roots = [(i, e) for i, e in roots
                 if e.get("invocation_id") in owners]
    selected: dict[str, Any] = (
        {"invocation_id": roots[0][1].get("invocation_id"), "index": roots[0][0]}
        if len(roots) == 1 else {})
    identity = selected.get("invocation_id")
    result = {"status": "unavailable", "text": None,
              "invocation_id": identity, "attempt_id": None}
    if not identity:
        return {**result, "cause": "invocation_missing"}
    invocation = [e for e in events[selected["index"]:]
                  if e.get("invocation_id") == identity]
    if not invocation or invocation[-1].get("kind") != "invocation_finished":
        return {**result, "cause": "invocation_unsettled"}
    calls = [e for e in invocation if e.get("kind") == "call_started"]
    # A sent reply ends the turn: the loop's next cycle is cancelled before
    # it reaches a transport — the settled attempt before it is terminal.
    last = None
    for call in reversed(calls):
        if any(e.get("kind") == "transport_started" for e in invocation
               if e.get("attempt_id") == call.get("attempt_id")):
            last = call
            break
        if call.get("cancelled") != "Reply already sent — the turn is finished.":
            break
    last = last or (calls[-1] if calls else None)
    attempt = last.get("attempt_id") if last else None
    result["attempt_id"] = attempt
    if not attempt:
        return {**result, "cause": "attempt_missing"}
    records = [e for e in invocation if e.get("attempt_id") == attempt]
    finished = [(i, e) for i, e in enumerate(records) if e.get("kind") == "call_finished"]
    if (not finished or finished[-1][1].get("retry") or finished[-1][1].get("error_type")
            or (last or {}).get("cancelled")):
        return {**result, "cause": "attempt_unsettled"}
    records = records[:finished[-1][0] + 1]
    transports = [i for i, e in enumerate(records) if e.get("kind") == "transport_started"]
    if not transports:
        return {**result, "cause": "response_missing"}
    transport = records[transports[-1]:]
    ends = [e for e in transport if e.get("kind") == "transport_finished"]
    if not ends or ends[-1].get("error_type"):
        return {**result, "cause": "transport_unsettled"}
    text: list[str] = []
    stop = None
    observed = False
    for event in transport:
        if event.get("kind") != "transport_response":
            continue
        for choice in (event.get("response") or {}).get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            message = choice.get("message") if "message" in choice else choice.get("delta")
            if not isinstance(message, dict):
                return {**result, "cause": "response_invalid"}
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                return {**result, "cause": "response_invalid"}
            if message.get("role") not in (None, "assistant"):
                return {**result, "cause": "response_invalid"}
            observed = True
            if content is not None:
                text.append(content)
            stop = choice.get("finish_reason") or stop
    if not observed or stop not in ("stop", "tool_calls", "function_call"):
        return {**result, "cause": "response_incomplete"}
    return {**result, "status": "observed", "text": "".join(text),
            "finish_reason": stop, "cause": None}


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
    """The owned replay session through settlement, or its incomplete terminal window."""
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
    terminal = None
    for i, event in enumerate(events[start:], start):
        if _body(event).get("session_id") != session_id:
            continue
        if event.get("name") == "nora.session.terminal":
            terminal = i
        if event.get("name") == "nora.session.settled":
            return events[start:i + 1]
    return events[start:terminal + 1] if terminal is not None else events[start:]


def turn_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A produced turn's whole evidence window since its declared send:
    admission, session, model requests, tool calls and transports as the
    App emitted them. Nothing is filtered — the window is provenance, and
    `bound_turn_events` names the subset this turn's own occasion caused."""
    return list(events)


def bound_turn_events(
    events: list[dict[str, Any]], *, event_id: str | None,
    session_id: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The subset of a turn's window its own declared input caused, plus
    the trace ids it was bound by.

    The occasion's own causal marker is the span attribute
    `nora.bridge.event_id`: the queue journal spans (`queue.claim`,
    `queue.attempt`, `queue.ack`) carry it, and so does the separate
    generation root the durable turn's model calls, tool acts and sends
    record under. A span naming the canonical event id belongs to this
    occasion wherever it sits — a `queue.attempt` root is never the
    model call's trace. The session its own stimulus settled to joins by
    the session's own identity. Everything else in the window — a
    reminder's fire, a background pass, a foreign occasion — stays
    provenance, never this turn's acts.
    """
    traces = sorted({
        event["trace_id"] for event in events
        if event_id is not None
        and (event.get("attributes") or {}).get("nora.bridge.event_id")
        == event_id
        and isinstance(event.get("trace_id"), str)
    })
    bound = [event for event in events
             if event.get("trace_id") in set(traces)]
    seen = {id(event) for event in bound}
    if session_id is not None:
        bound.extend(
            event for event in session_events(events, session_id=session_id)
            if id(event) not in seen)
    return bound, traces


def wake_trajectory(
    events: list[dict[str, Any]], *, wake_id: int | None,
    session_id: str | None, tokens: int | None,
) -> dict[str, Any]:
    """The session's events for one wake, grouped for a judge to read."""
    started: dict[str, Any] = {}
    instructions: dict[str, Any] = {}
    terminal: dict[str, Any] = {}
    settlement: dict[str, Any] = {"status": "unavailable", "cause": "settlement_not_captured"}
    terminal_index = len(events)
    model: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for index, entry in enumerate(events):
        name = entry.get("name")
        if name == "nora.session.started":
            started = _body(entry)
        elif name == "nora.session.instructions":
            instructions = _body(entry)
        elif name == "nora.session.terminal":
            terminal = _body(entry)
            terminal_index = index
        elif name == "nora.session.settled":
            settlement = {"status": "observed", **_body(entry)}
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
    prefetch = (
        {"keys": started["graph_keys"], "relations": started["graph_relations"]}
        if "graph_keys" in started or "graph_relations" in started else None
    )
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
        "prefetch": prefetch,
        "model_calls": model,
        "final_response": terminal_response(events[:terminal_index + 1]),
        "settlement": settlement,
        "exchanges": evidence_exchanges(events),
        "tool_calls": tools,
        "stop_reason": terminal.get("outcome") or terminal.get("stop_reason"),
        "usage": {"tokens": tokens},
    }
