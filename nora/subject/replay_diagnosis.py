"""Validate per-statement diagnostics over the existing private evidence."""

from __future__ import annotations

import json
from typing import Any

OBSERVATIONS = frozenset(
    {
        "uncited_detail",
        "inference_as_said",
        "wrong_subject",
        "other",
        "rejected_as_not_memory",
        "mentioned_without_statement",
        "refused_not_retried",
        "named_bound",
        "not_mentioned",
        "insufficient_evidence",
    }
)


def diagnose(
    statement: dict[str, Any], events: list[dict[str, Any]], verdict: Any
) -> dict[str, Any]:
    """Fail closed on missing reasoning, invalid shape or ungrounded references.

    This does not score support or recall. The supplied judge reads a statement
    and the complete record; this validator checks the evidence it cites exists.
    """
    insufficient = {"observation": "insufficient_evidence", "event_ids": []}
    availability = [
        e.get("reasoning_availability") for e in events if e["kind"] == "transport_finished"
    ]
    if not availability or any(a != "observed" for a in availability):
        return insufficient
    if not isinstance(verdict, dict) or verdict.get("observation") not in OBSERVATIONS:
        return insufficient
    ids = verdict.get("event_ids")
    if not isinstance(ids, list) or not ids or not verdict.get("reason"):
        return insufficient
    by_id = {e["event_id"]: e for e in events}
    if any(not isinstance(i, str) or i not in by_id for i in ids):
        return insufficient
    selected = [by_id[i] for i in ids]
    observation = verdict["observation"]
    if observation == "named_bound":
        terminals = [e for e in selected if e.get("name") == "nora.session.terminal"]
        if not any(json.loads(e.get("attributes", {}).get("body", "{}")).get("bound")
                   for e in terminals):
            return insufficient
    if observation == "rejected_as_not_memory":
        quote = verdict.get("quote")
        if not isinstance(quote, str) or not quote or not any(
            quote in json.dumps(e, ensure_ascii=False) for e in selected
            if e["kind"] == "transport_response"
        ):
            return insufficient
    if observation == "refused_not_retried":
        tools = [
            e
            for e in events
            if e["kind"] == "tool_finished" and e.get("tool", {}).get("name") == "memory_propose"
        ]
        if len(tools) != 1 or tools[0] not in selected:
            return insufficient
    if statement.get("kind") == "missed" and observation in {
        "uncited_detail",
        "inference_as_said",
        "wrong_subject",
    }:
        return insufficient
    return {**verdict, "statement": statement}
