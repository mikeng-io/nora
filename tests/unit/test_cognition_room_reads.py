"""Each agenda item's reads resolve through that item's own room (DEC-231, R-21).

A multi-room agenda is not bound to the unit's HOME room: a read about another
room's people must not fail closed on the wrong roster or answer through the
wrong room. The session holds ONE tool per name that takes a `room` argument
and routes the read to that room's own build; a room that is not on the agenda
is refused. The tools here are REAL Strands tools (built the way the
registry builds them, `nora.agent.ports.adapt`) called through their real
`stream`, so the schema the model reads and the routing it gets are what ship.

What each test would catch if the fix were reverted is named in
`scripts/mutations/cognition-step3a3-review.json`.
"""

from __future__ import annotations

import json
from typing import Any

from nora.agent.ports import ToolSpec, adapt
from nora.agent.room_routing import route_reads
from nora.app.cognition_wiring import read_tools_for
from nora.domain.cognition_session import Room

A = Room("signal", "room-a")
B = Room("discord", "room-b")
C = Room("whatsapp", "room-c")          # in the unit, NOT on the agenda


def _tool_for(name: str, room: Room, calls: list[tuple[str, str, dict[str, Any]]]) -> Any:
    """A read tool whose answer says which room it was built for."""
    async def call(query: str, days: int = 7) -> dict[str, Any]:
        """Look.

        Args:
            query: What to look for.
            days: How far back.
        """
        calls.append((name, f"{room.platform}:{room.conversation_id}", {"query": query}))
        return {"status": "ok", "through": f"{room.platform}:{room.conversation_id}"}

    call.__name__ = name
    return adapt(ToolSpec(name=name, description="", schema={}, call=call, budget=5))


async def _call(tool: Any, **arguments: Any) -> dict[str, Any]:
    events = [e async for e in tool.stream({"toolUseId": "t1", "name": tool.tool_name,
                                            "input": arguments}, {})]
    result = events[-1].tool_result
    assert result["status"] == "success"
    return json.loads(result["content"][0]["text"])


def _routed(calls: list[Any]) -> dict[str, Any]:
    per_room = {
        f"{r.platform}:{r.conversation_id}": [
            _tool_for("note_read", r, calls), _tool_for("record_query", r, calls),
        ]
        for r in (A, B)
    }
    return {t.tool_name: t for t in route_reads(per_room)}


async def test_a_two_room_agenda_reads_each_room_through_itself():
    calls: list[Any] = []
    tools = _routed(calls)
    assert set(tools) == {"note_read", "record_query"}           # ONE per name
    assert (await _call(tools["record_query"], room="signal:room-a", query="x"))["through"] == (
        "signal:room-a")
    assert (await _call(tools["record_query"], room="discord:room-b", query="y"))["through"] == (
        "discord:room-b")
    assert calls == [
        ("record_query", "signal:room-a", {"query": "x"}),
        ("record_query", "discord:room-b", {"query": "y"}),
    ]                                                           # and `room` never reaches the read


async def test_a_room_not_on_the_agenda_is_refused_and_reads_nothing():
    calls: list[Any] = []
    tools = _routed(calls)
    answer = await _call(tools["note_read"], room="whatsapp:room-c", query="x")
    assert answer["status"] == "refused" and answer["reason"] == "unknown_room"
    assert "signal:room-a" in answer["detail"] and "whatsapp:room-c" in answer["detail"]
    assert calls == []


async def test_a_call_that_names_no_room_is_refused_never_guessed_from_the_first():
    calls: list[Any] = []
    tools = _routed(calls)
    answer = await _call(tools["note_read"], query="x")
    assert answer["status"] == "refused" and answer["reason"] == "room_required"
    assert calls == []


def test_the_schema_the_model_reads_lists_the_agenda_rooms_and_requires_one():
    spec = _routed([])["note_read"].tool_spec
    schema = spec["inputSchema"]["json"]
    assert schema["properties"]["room"]["enum"] == ["signal:room-a", "discord:room-b"]
    assert "room" in schema["required"] and "query" in schema["required"]
    assert schema["properties"]["query"]                          # the tool's own arguments kept
    assert spec["name"] == "note_read"


def test_the_underlying_tools_schema_is_not_touched():
    calls: list[Any] = []
    per_room = {"signal:room-a": [_tool_for("note_read", A, calls)],
                "discord:room-b": [_tool_for("note_read", B, calls)]}
    before = json.dumps(per_room["signal:room-a"][0].tool_spec, sort_keys=True)
    route_reads(per_room)
    assert json.dumps(per_room["signal:room-a"][0].tool_spec, sort_keys=True) == before


async def test_a_name_one_room_does_not_hold_is_refused_for_that_room_only():
    calls: list[Any] = []
    tools = {t.tool_name: t for t in route_reads({
        "signal:room-a": [_tool_for("note_read", A, calls), _tool_for("record_query", A, calls)],
        "discord:room-b": [_tool_for("note_read", B, calls)],
    })}
    ok = await _call(tools["record_query"], room="signal:room-a", query="x")
    assert ok["status"] == "ok"
    answer = await _call(tools["record_query"], room="discord:room-b", query="x")
    assert answer["status"] == "refused" and answer["reason"] == "unavailable"


# ---- the wiring: one room untouched, several routed, the home room never substituted ----

class _Source:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.built: list[tuple[str, str, dict[str, Any]]] = []

    def build(self, **kw: Any) -> list[Any]:
        self.calls.append(kw)
        room = next(r for r in (A, B, C) if r.conversation_id == kw["key"])
        return [_tool_for(n, room, self.built) for n in sorted(kw["names"])]


def test_one_room_gets_the_registrys_tools_untouched_and_several_get_routed_ones():
    source = _Source()
    build = read_tools_for(source, names=frozenset({"note_read"}))
    (single,) = build("u", [A])
    assert "room" not in single.tool_spec["inputSchema"]["json"]["properties"]
    routed = build("u", [A, B])
    assert [t.tool_name for t in routed] == ["note_read"]
    assert "room" in routed[0].tool_spec["inputSchema"]["json"]["properties"]
    # each agenda room was built through the registry's own source, scope-keyed by ITS id
    assert [c["key"] for c in source.calls] == ["room-a", "room-a", "room-b"]
    assert {c["adaptor"] for c in source.calls} == {"cognition"}
    assert all(c["unit"] is None for c in source.calls)


async def test_the_routed_reads_of_the_wiring_reach_the_room_they_name_and_not_the_home_room():
    source = _Source()
    (tool,) = read_tools_for(source, names=frozenset({"note_read"}))("u", [A, B])
    assert (await _call(tool, room="discord:room-b", query="x"))["through"] == "discord:room-b"
    assert [b[1] for b in source.built] == ["discord:room-b"]
