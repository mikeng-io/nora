"""Compare only the system/agenda projection, after transport normalization."""

from __future__ import annotations

from typing import Any

from .punctuation import undash


def compare_inspection(inspection: dict[str, Any], request: dict[str, Any]) -> bool:
    system = "\n".join(m["content"] for m in request["messages"] if m["role"] == "system")
    agenda = next((m["content"] for m in request["messages"] if m["role"] == "user"), None)
    if isinstance(agenda, list):
        agenda = "".join(p.get("text", "") for p in agenda)
    return system == undash(inspection["system"]) and agenda == undash(inspection["agenda"])
