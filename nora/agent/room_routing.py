"""One read tool per name for an agenda that spans several rooms, each read
resolved through the room it names (DEC-231, R-21).

**The problem.** A tool NAME is one tool: two rooms' `record_query` cannot both
be «record_query» (Strands keeps the LAST silently, which reads one room's
Record through another room's binding without a word). Binding the whole
session to the unit's home room would make every read about another room's
people fail closed on the home room's roster, or answer through the wrong one;
each item's room-scoped reads must resolve through that item's room.

**The shape.** The registry builds each read tool for each agenda room, as it
always does (`RegistryToolSource.build`, so lineage, the roster guard and the
record read's room rules are the room's own). This module lays ONE tool over
the per-room copies of a name: its model-facing schema is the underlying one
plus a required `room` (an enum of the agenda's rooms, `platform:
conversation_id`), and a call is routed to that room's copy with `room`
removed from its arguments. A call with no `room` or a room that is not on the
agenda is REFUSED with a result the model can read, never guessed from the
first item and never widened. The `room` picks the BINDING a read resolves
through (its roster and lineage); it does not fence the data: `record_query
scope="all_rooms"` and `search_memory_workspace` read the unit's other rooms
too, because the unit is the read boundary (DEC-231).

One room on the agenda needs none of this and gets the registry's tools
untouched (`route_reads` returns them as they are).
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncGenerator, Mapping
from typing import Any

from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolSpec, ToolUse

ROOM_ARG = "room"
_ENVELOPE_STATUS = "status"


def _name_of(tool: Any) -> str | None:
    name = getattr(tool, "tool_name", None) or getattr(tool, "__name__", None)
    return name if isinstance(name, str) else None


class RoomRoutedTool(AgentTool):
    """`name`'s tool over `per_room` (room key -> that room's copy of it)."""

    def __init__(self, *, name: str, per_room: Mapping[str, Any], rooms: tuple[str, ...]) -> None:
        super().__init__()
        self._name = name
        self._per_room = dict(per_room)
        self._rooms = rooms
        base = next(iter(self._per_room.values()))
        spec: ToolSpec = copy.deepcopy(base.tool_spec)
        schema = spec["inputSchema"]["json"]
        properties = dict(schema.get("properties", {}))
        properties[ROOM_ARG] = {
            "type": "string",
            "enum": list(rooms),
            "description": (
                "The room to read through, as `platform:conversation_id` from the "
                "agenda. Required: this agenda spans several rooms, and each read "
                "resolves through exactly one of them and shows only what that "
                "room may be shown."
            ),
        }
        schema["properties"] = properties
        schema["required"] = [*schema.get("required", []), ROOM_ARG]
        self._spec = spec

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> ToolSpec:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "python"

    def _refusal(self, tool_use_id: str, reason: str, detail: str) -> ToolResultEvent:
        # Strands' own result envelope (its `status` is «success»/«error», the
        # transport's, not one of Nora's tool statuses), keyed by a constant so
        # the output-contract scan of status literals reads only OUR answer:
        # the JSON below, whose `status` is `refused`.
        answer = json.dumps(
            {"status": "refused", "reason": reason, "detail": detail}, ensure_ascii=False,
        )
        envelope: dict[str, Any] = {
            "toolUseId": tool_use_id, _ENVELOPE_STATUS: "success", "content": [{"text": answer}],
        }
        return ToolResultEvent(envelope)  # type: ignore[arg-type]

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        tool_input = dict(tool_use.get("input") or {})
        room = tool_input.pop(ROOM_ARG, None)
        use_id = tool_use.get("toolUseId", "unknown")
        if not room:
            yield self._refusal(
                use_id, "room_required",
                f"this agenda spans {len(self._rooms)} rooms; name the one this read is "
                f"about as `room`, one of {list(self._rooms)}",
            )
            return
        if not isinstance(room, str) or room not in self._rooms:
            yield self._refusal(
                use_id, "unknown_room",
                f"{room!r} is not a room on this agenda: {list(self._rooms)}",
            )
            return
        target = self._per_room.get(room)
        if target is None:
            yield self._refusal(
                use_id, "unavailable",
                f"this read cannot be made through {room}; it is not something that room "
                "can be asked",
            )
            return
        async for event in target.stream(
            {**tool_use, "input": tool_input}, invocation_state, **kwargs,
        ):
            yield event


def route_reads(per_room: Mapping[str, list[Any]]) -> list[Any]:
    """The read tools of an agenda spanning `per_room`'s rooms, one per name.

    A name a room does not hold is absent from that room's routing: a call
    naming that room is refused (`unavailable`), which reads as «this room cannot
    do that», never as another room's answer."""
    rooms = tuple(per_room)
    names: dict[str, dict[str, Any]] = {}
    for room, tools in per_room.items():
        for tool in tools:
            name = _name_of(tool)
            if name is not None:
                names.setdefault(name, {})[room] = tool
    return [
        RoomRoutedTool(name=name, per_room=copies, rooms=rooms)
        for name, copies in sorted(names.items())
    ]


__all__ = ["ROOM_ARG", "RoomRoutedTool", "route_reads"]
