"""Canonical request custody, census and settled wake instruction receipts."""

import json
from collections import Counter
from typing import Any

from nora.subject.raw_model import instruction_messages


def request_census(events: list[dict[str, Any]], *, judges: list[str]) -> dict[str, Any]:
    """Count captured formatted requests, not inferred dispatches or billed usage."""
    models: Counter[str] = Counter()
    refs: list[str] = []
    for event in events:
        if event.get("name") != "nora.model.request":
            continue
        try:
            body = json.loads(event["attributes"]["nora.model.request.body"])
            model = body["model"]
            ref = event.get("event_id")
            if not isinstance(model, str) or not model or not isinstance(ref, str) or not ref:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return {"status": "unavailable", "cause": "model_request_census_invalid",
                    "model_requests": None, "judge_requests": None}
        models[model] += 1
        refs.append(ref)
    return {"status": "observed", "basis": "captured-formatted-requests",
            "model_requests": sum(models.values()),
            "judge_requests": sum(models[model] for model in set(judges)),
            "by_model": dict(models), "refs": refs}


def subject_instructions(trajectory: dict[str, Any]) -> dict[str, Any]:
    requests = trajectory.get("model_calls")
    observed = instruction_messages(requests)
    if observed["status"] == "ok" and observed["messages"]:
        return {**observed, "status": "observed"}
    effects = trajectory.get("effects") or {}
    wake_id = trajectory.get("wake_id")
    unit = trajectory.get("unit")
    if (requests == [] and "session_id" in trajectory and trajectory["session_id"] is None
            and effects.get("status") == "observed" and type(wake_id) is int
            and isinstance(unit, str) and unit and effects.get("unit") == unit):
        for item in effects.get("items") or []:
            row = item.get("row") or {}
            if (item.get("source") == "cognition_wake_outcome"
                    and type(row.get("wake_id")) is int and row["wake_id"] == wake_id
                    and row.get("unit") == unit
                    and "session_id" in row and row["session_id"] is None
                    and row.get("outcome") == "closed"
                    and isinstance(item.get("ref"), str) and item["ref"]):
                return {"status": "observed", "mode": "no_session", "messages": [],
                        "ref": item["ref"]}
    return {"status": "unavailable", "cause": "subject_instructions_not_captured", "messages": None}
