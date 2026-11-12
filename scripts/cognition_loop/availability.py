"""What the composed subject could reach: the switches Cognition's reads
depend on, the tools a session was offered, and the calls that answered
`unavailable` — so a closed read is visible before any judgement."""

from __future__ import annotations

from typing import Any

#: The config switches a Cognition read or write depends on.
GATES = (
    "memory.index.read", "memory.profile.read", "memory.profile.write",
    "memory.graph.enabled", "lessons.enabled", "culture.enabled",
    "documents.enabled", "notes.enabled", "standing.enabled",
    "social.enabled", "social.poll.enabled", "social.instagram.enabled",
    "presence.enabled", "search.enabled", "online.enabled",
    "platforms.signal.enabled", "platforms.discord.enabled",
    "platforms.whatsapp.enabled", "platforms.instagram.enabled",
)


def _value(cfg: Any, dotted: str) -> Any:
    node = cfg
    for part in dotted.split("."):
        node = getattr(node, part, None)
        if node is None:
            return None
    return node


def switches(cfg: Any) -> dict[str, Any]:
    return {gate: _value(cfg, gate) for gate in GATES}


def offered_tools(trajectory: dict[str, Any]) -> list[str] | None:
    """The tool names the session's first model request carried."""
    for call in trajectory.get("model_calls") or []:
        tools = (call or {}).get("tools")
        if tools:
            return sorted(
                str((t.get("function") or t).get("name")) for t in tools
                if isinstance(t, dict))
    return None


def unavailable_calls(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"tool": c.get("tool"), "reason": c.get("reason")}
        for c in trajectory.get("tool_calls") or []
        if c.get("status") == "unavailable"
    ]


def availability(cfg: Any, trajectory: dict[str, Any]) -> dict[str, Any]:
    return {
        "switches": switches(cfg),
        "offered": offered_tools(trajectory),
        "unavailable": unavailable_calls(trajectory),
    }
