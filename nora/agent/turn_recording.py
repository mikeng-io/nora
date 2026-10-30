"""Capture source requirements beside each SDK call, without changing its result."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from nora.agent.ports import ToolSpec
from nora.domain.lineage import message, room_key
from nora.domain.turn_record import DisclosureRequirement

_CURRENT: ContextVar[list[DisclosureRequirement] | None] = ContextVar(
    "turn_item_requirements", default=None,
)

# Sources whose only disclosure boundary is the room they ran in. All other
# tools must record their source's gates; an unmodelled gate stays withheld.
_ROOM_SOURCES = frozenset({
    "now", "calculate", "transit", "route", "place_info", "weather",
    "web_search", "web_fetch", "parking_status", "find_parking",
    "reverse_geocode", "search_gifs", "send_reply", "send_gif", "media_send",
    # `decision_conclude` records a requirement for every source it cites
    # itself; a source-less call's only gate is the room it ran in.
    "decision_conclude",
})


def record_requirement(requirement: DisclosureRequirement) -> None:
    current = _CURRENT.get()
    if current is not None:
        current.append(requirement)


class TurnRecording(HookProvider):
    def __init__(self) -> None:
        self.requirements: dict[str, list[DisclosureRequirement]] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def before(self, event: BeforeToolCallEvent) -> None:
        held: list[DisclosureRequirement] = []
        self.requirements[event.tool_use["toolUseId"]] = held
        _CURRENT.set(held)

    def after(self, event: AfterToolCallEvent) -> None:
        _CURRENT.set(None)

    def to_json(self) -> dict[str, Any]:
        return {key: [r.to_json() for r in held] for key, held in self.requirements.items()}


def in_room(spec: ToolSpec, *, platform: str, conversation_id: str) -> ToolSpec:
    """The tool's actual bound room, including Cognition's room-dispatched reads."""
    origin = room_key(platform, conversation_id)

    @wraps(spec.call)
    async def call(*args: Any, **kwargs: Any) -> Any:
        current = _CURRENT.get()
        before = len(current) if current is not None else 0
        record_requirement(DisclosureRequirement(origin, (message(origin),)))
        try:
            return await spec.call(*args, **kwargs)
        finally:
            if (current is not None and len(current) == before + 1
                    and spec.name not in _ROOM_SOURCES):
                record_requirement(DisclosureRequirement(None, (), resolved=False))

    return replace(spec, call=call)
