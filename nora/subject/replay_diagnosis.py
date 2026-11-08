"""Validate per-statement diagnostics over the existing private evidence."""

from __future__ import annotations

import json
import re
from typing import Any

from nora.language.cognition_render import parse_rendered_agenda

OBSERVATIONS_BY_KIND = {
    "unsupported": frozenset({
        "uncited_detail",
        "inference_as_said",
        "wrong_subject",
        "other",
        "insufficient_evidence",
    }),
    "missed": frozenset({
        "rejected_as_not_memory",
        "mentioned_without_statement",
        "refused_not_retried",
        "named_bound",
        "not_mentioned",
        "other",
        "insufficient_evidence",
    }),
}
OBSERVATIONS = frozenset().union(*OBSERVATIONS_BY_KIND.values())


# ponytail: fixed final-prompt byte ceiling; revise only from audited judge-input receipts.
MAX_DIAGNOSTIC_INPUT_BYTES = 64_000


def cited_source_numbers(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(cited_source_numbers(v) for v in value))
    if isinstance(value, dict):
        own = {str(c["source"]) for c in value.get("cite", [])
               if isinstance(c, dict) and "source" in c}
        return own | set().union(*(cited_source_numbers(v) for v in value.values()))
    return set()


def missed_source_numbers(entry: dict[str, Any]) -> list[str]:
    """Structured ids are authoritative; old captures may name rows in prose."""
    if "sources" in entry:
        source_ids = entry["sources"]
        if not isinstance(source_ids, list) or any(type(n) is not int for n in source_ids):
            return []
        return list(dict.fromkeys(str(n) for n in source_ids))
    location = str(entry.get("location", "")).strip()
    ids = r"-?\d+(?:\s*(?:/|,|\band\b)\s*-?\d+)*"
    if re.fullmatch(ids, location):
        references = location
    else:
        references = " / ".join(re.findall(
            rf"\b(?:new\s+)?(?:source(?:\s+row)?|message|row)\s*(?:[=:]\s*)?"
            rf"({ids})(?!\w|[.-]\d)",
            location, re.IGNORECASE))
    return list(dict.fromkeys(re.findall(r"-?\d+", references)))



def _items(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"index": int(item.attributes.get("index", str(index))),
             "rendered": item.rendered, "rows": item.rows,
             "numbers": {row.attributes["n"] for row in item.rows
                         if not row.attributes.get("reaction")}}
            for index, item in enumerate(parse_rendered_agenda(request))]


def _proposal_end(statement: dict[str, Any], events: list[dict[str, Any]]) -> tuple[int, set[int]]:
    if statement["kind"] != "unsupported":
        terminal = next((i for i, e in enumerate(events)
                         if e.get("name") == "nora.session.terminal"), len(events) - 1)
        return terminal, set()
    found = []
    for index, event in enumerate(events):
        if (event["kind"] != "tool_finished"
                or event.get("tool", {}).get("name") != "memory_propose"):
            continue
        tool = event["tool"]
        inputs = tool["input"].get("statements", [])
        result = event.get("result", {})
        if (result.get("status") == "error" or not isinstance(inputs, list)
                or statement["statement"] not in inputs):
            continue
        result = _proposal_result(result)
        for saved in result.get("statements", []):
            slot = saved.get("index")
            if (saved.get("status") == "saved" and isinstance(slot, int)
                    and 0 <= slot < len(inputs) and inputs[slot] == statement["statement"]):
                found.append((index, set(tool["input"].get("items", []))))
    if len(found) != 1:
        raise ValueError("diagnostic_proposal_missing_or_ambiguous")
    return found[0]


def _append_response(call: dict[str, Any], event: dict[str, Any]) -> None:
    call["last_response_event_id"] = event["event_id"]
    for choice in event.get("response", {}).get("choices", []):
        part = choice.get("delta") or choice.get("message") or {}
        call["reasoning"].append(part.get("reasoning_content") or part.get("reasoning") or "")
        call["assistant_text"].append(part.get("content") or "")
        for index, tool in enumerate(part.get("tool_calls") or []):
            target = call["tool_calls"].setdefault(tool.get("index", index),
                {"id": "", "name": [], "arguments": []})
            if tool.get("id"):
                target["id"] = tool["id"]
            fn = tool.get("function") or {}
            target["name"].append(fn.get("name") or "")
            target["arguments"].append(fn.get("arguments") or "")
        if choice.get("finish_reason"):
            call["finish_reason"] = choice["finish_reason"]


def compact_trajectory(statement: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Project one item's call interval; assemble chunks once, retaining real source anchors."""
    end, item_indices = _proposal_end(statement, events)
    numbers = (cited_source_numbers(statement["statement"]) if statement["kind"] == "unsupported"
               else set(missed_source_numbers(statement)))
    shown = None
    selected = []
    for index, event in enumerate(events[:end + 1]):
        if event.get("name") != "nora.model.request":
            continue
        request = json.loads(event["attributes"]["nora.model.request.body"])
        items = _items(request)
        selected = [item for item in items if (item["index"] in item_indices if item_indices
                                               else bool(item["numbers"] & numbers))]
        if selected:
            shown = index
            break
    if shown is None:
        raise ValueError("diagnostic_item_not_shown")
    indices = {item["index"] for item in selected}
    agenda = [{"index": item["index"], "rendered": item["rendered"],
               "event_id": events[shown]["event_id"]} for item in selected]
    cited = {}
    for event in events[:end + 1]:
        if event.get("name") == "nora.proposal.sources":
            rows = json.loads(event["attributes"]["body"])
            cited.update({n: {**row, "event_id": event["event_id"]}
                          for n, row in rows.items() if n in numbers})
    for item in selected:
        for row in item["rows"]:
            n = row.attributes["n"]
            if n in numbers and not row.attributes.get("reaction"):
                cited[n] = {"rendered": row.rendered, "event_id": events[shown]["event_id"]}
    calls = []
    active: dict[tuple[Any, Any], dict[str, Any]] = {}
    tools = []
    terminal = []
    for event in events[shown:end + 1]:
        key = (event.get("invocation_id"), event.get("attempt_id"))
        kind = event["kind"]
        if kind == "transport_started":
            call = {"kind": "model_call", "event_id": event["event_id"],
                    "attempt_id": event.get("attempt_id"), "reasoning": [], "assistant_text": [],
                    "tool_calls": {}, "reasoning_availability": "capture_unavailable"}
            active[key] = call
            calls.append(call)
        elif kind == "transport_response" and key in active:
            _append_response(active[key], event)
        elif kind == "transport_finished" and key in active:
            call = active.pop(key)
            call["last_event_id"] = event["event_id"]
            call["reasoning_availability"] = event.get(
                "reasoning_availability", "capture_unavailable")
            call["reasoning"] = "".join(call["reasoning"])
            call["assistant_text"] = "".join(call["assistant_text"])
            call["tool_calls"] = [{"id": tool["id"], "name": "".join(tool["name"]),
                                   "arguments": "".join(tool["arguments"])}
                                  for tool in call["tool_calls"].values()]
        elif kind == "tool_finished":
            item_ids = set(event.get("tool", {}).get("input", {}).get("items", []))
            if not item_ids or item_ids & indices:
                tools.append({k: event[k] for k in ("kind", "event_id", "tool", "result")})
        elif event.get("name") == "nora.session.terminal":
            body = json.loads(event.get("attributes", {}).get("body", "{}"))
            terminal.append({"kind": "event", "event_id": event["event_id"],
                             "name": event["name"], "attributes": {"body": json.dumps({
                                 k: body.get(k) for k in ("bound", "outcome", "error_type")})}})
    if active:
        raise ValueError("diagnostic_call_incomplete")
    order = {e["event_id"]: index for index, e in enumerate(events)}
    compact = sorted([*calls, *tools, *terminal], key=lambda e: order[e["event_id"]])
    return {"agenda_items": agenda, "cited_rows": cited, "events": compact,
            "first_event_id": events[shown]["event_id"], "last_event_id": events[end]["event_id"]}


def diagnose(
    statement: dict[str, Any], trajectory: dict[str, Any], verdict: Any
) -> dict[str, Any]:
    """Fail closed on missing reasoning, invalid shape or ungrounded references.

    This does not score support or recall. The supplied judge reads a statement
    and the compact trajectory; this validator checks the evidence it cites was shown.
    """
    insufficient = {"observation": "insufficient_evidence", "event_ids": []}
    events = trajectory["events"]
    availability = [
        e.get("reasoning_availability") for e in events
        if e["kind"] == "model_call"
    ]
    if not any(a == "observed" for a in availability):
        return insufficient
    allowed = OBSERVATIONS_BY_KIND.get(statement.get("kind", ""), frozenset())
    if not isinstance(verdict, dict) or verdict.get("observation") not in allowed:
        return insufficient
    ids = verdict.get("event_ids")
    if not isinstance(ids, list) or not ids or not verdict.get("reason"):
        return insufficient
    by_id = {e["event_id"]: e for e in events}
    for event in events:
        for anchor in ("last_response_event_id", "last_event_id"):
            if anchor in event:
                by_id[event[anchor]] = event
    for row in [*trajectory.get("agenda_items", []),
                *trajectory.get("cited_rows", {}).values()]:
        by_id[row["event_id"]] = {**row, "kind": "source_row"}
    if any(not isinstance(i, str) or i not in by_id for i in ids):
        return insufficient
    selected = [by_id[i] for i in ids]
    observation = verdict["observation"]
    if any(e["kind"] == "model_call" and e["reasoning_availability"] != "observed"
           for e in selected):
        return insufficient
    if observation == "not_mentioned" and any(
        e["kind"] != "model_call" for e in selected
    ):
        return insufficient
    if observation == "named_bound":
        terminals = [e for e in selected if e.get("name") == "nora.session.terminal"]
        if not any(json.loads(e.get("attributes", {}).get("body", "{}")).get("bound")
                   for e in terminals):
            return insufficient
    if observation == "rejected_as_not_memory":
        quote = verdict.get("quote")
        if not isinstance(quote, str) or not quote or not any(
            quote in e.get("reasoning", "") for e in selected
            if e["kind"] == "model_call"
        ):
            return insufficient
    if observation == "refused_not_retried":
        tools = [
            e
            for e in selected
            if e["kind"] == "tool_finished" and e.get("tool", {}).get("name") == "memory_propose"
        ]
        if not any(_proposal_refused(e) for e in tools):
            return insufficient
    return {**verdict, "statement": statement}


def _proposal_refused(event: dict[str, Any]) -> bool:
    result = event["result"]
    if result.get("status") == "error":
        return False
    result = _proposal_result(result)
    return result.get("status") == "refused" or any(
        slot.get("status") == "refused" for slot in result.get("statements", []))


def _proposal_result(result: dict[str, Any]) -> dict[str, Any]:
    if "content" in result:
        return next((p["json"] if "json" in p else json.loads(p["text"])
                     for p in result["content"] if "json" in p or "text" in p), {})
    return result
