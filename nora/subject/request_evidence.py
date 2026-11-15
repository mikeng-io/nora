"""Subject-visible instruction custody over canonical requests and settled wake receipts."""

from typing import Any

from nora.subject.raw_model import instruction_messages


def subject_instructions(trajectory: dict[str, Any]) -> dict[str, Any]:
    requests = trajectory.get("model_calls")
    observed = instruction_messages(requests)
    if observed["status"] == "observed" and observed["messages"]:
        return observed
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
