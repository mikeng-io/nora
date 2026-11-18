"""Read and compare actual subject instructions and assembled context at transport boundaries."""

from __future__ import annotations

from typing import Any

from .punctuation import undash


def compare_inspection(inspection: dict[str, Any], request: dict[str, Any]) -> bool:
    system = "\n".join(m["content"] for m in request["messages"] if m["role"] == "system")
    agenda = next((m["content"] for m in request["messages"] if m["role"] == "user"), None)
    if isinstance(agenda, list):
        agenda = "".join(p.get("text", "") for p in agenda)
    return system == undash(inspection["system"]) and agenda == undash(inspection["agenda"])


def instruction_messages(requests: Any) -> dict[str, Any]:
    """The instruction roles from the first actual formatted request, without reconstruction."""
    if not isinstance(requests, list) or not requests:
        return {"status": "unavailable", "cause": "request_not_captured", "messages": None}
    first = requests[0]
    messages = first.get("messages") if isinstance(first, dict) else None
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        return {"status": "unavailable", "cause": "request_invalid", "messages": None}
    return {"status": "observed", "messages": [
        m for m in messages if m.get("role") in ("system", "developer")]}
