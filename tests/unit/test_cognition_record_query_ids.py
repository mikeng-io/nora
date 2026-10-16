"""`record_query` hands the Cognition session the number a note cites (DEC-250).

The session's fetched-rows watcher reads each message row's `n` and `sender`;
a tool that printed neither would get a note citing a message it had read
refused `unknown_id`. These tests run the REAL tool, built through the real
`RegistryToolSource` for the cognition adaptor and through `build_tools` for a
tag turn, against an in-memory Record read: the cognition build prints `n`, the
tag turn never does (R-18).

What each test catches when reverted is named in
`scripts/mutations/cognition-c.json`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from nora.agent.dreamer import HomeRoom, RegistryToolSource, cognition_grant
from nora.agent.fetch_watch import Fetched, watch_reads
from nora.agent.lineage import LineagePolicy
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.record_query import RecordHit
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore
from tests.fakes import SinkSender

NOW = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)
ROOM = "room-a"
MESSAGE_ID = 4242

_POLICY = f'''
default = "deny"

[[conversations]]
platform = "signal"
id = "{ROOM}"
label = "A room"
persons = ["p1"]
unit = "u1"
'''

_IDENTITY = '''
[persons.p1]
name = "Ada"
[[persons.p1.signal]]
phone = "+85200000001"
'''


class _Record:
    """The Record read, with one recorded message in the room."""

    async def query(self, q: Any) -> tuple[tuple[RecordHit, ...], bool, frozenset[int]]:
        row = TranscriptRow(
            id=MESSAGE_ID, sender_id="+85200000001", sender_name="Ada",
            ts=int(NOW.timestamp() * 1000) - 3_600_000, body="we are going",
            is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
            person_id="p1",
        )
        return (RecordHit(platform="signal", conversation_id=ROOM, row=row),), False, frozenset()


@pytest.fixture
def policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(_POLICY)
    return load_policy(path)


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(_IDENTITY)
    return load_persons(path)


async def _stream(tool: Any, **arguments: Any) -> dict[str, Any]:
    events = [e async for e in tool.stream(
        {"toolUseId": "t1", "name": tool.tool_name, "input": arguments}, {"agent": None},
    )]
    return json.loads(events[-1].tool_result["content"][0]["text"])  # type: ignore[no-any-return]


def _cognition_tool(policy, persons) -> Any:
    source = RegistryToolSource(
        registry=ToolRegistry(record=_Record(), lineage=LineagePolicy.from_policy(policy)),
        store=FakeRecordStore(), persons=persons, honcho=FakeMemoryStore(),
        metrics=Metrics(),
        scope_rooms={ROOM: HomeRoom(unit="u1", platform="signal", conversation_id=ROOM)},
    )
    built = source.build(
        key=ROOM, names=frozenset({"record_query"}), unit=None,
        adaptor="cognition", grant=cognition_grant(),
    )
    return {t.tool_name: t for t in built}["record_query"]


def _tag_tool(policy, persons) -> Any:
    tools = build_tools(
        store=FakeRecordStore(), conversation_id=ROOM, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(),
        room=Room(platform="signal", conversation_id=ROOM, roster_source=None, persons=persons),
        transit_source=TransitClient(
            kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
            mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
        ),
        clock=lambda: NOW, record=_Record(), lineage=LineagePolicy.from_policy(policy),
    )
    return {t.tool_name: t for t in tools}["record_query"]


async def test_the_cognition_sessions_record_query_rows_carry_their_number(policy, persons):
    answer = await _stream(_cognition_tool(policy, persons), start="2026-09-28")
    assert answer["status"] == "ok", answer
    [row] = answer["messages"]
    assert row["n"] == MESSAGE_ID and row["sender"] == "Ada"


async def test_what_the_real_record_query_returns_is_what_the_watcher_keeps(policy, persons):
    """The end of the chain: the REAL tool's output, through the watcher, is a
    citable id with its sender."""
    fetched = Fetched()
    room_key = f"signal:{ROOM}"
    [watched] = watch_reads([_cognition_tool(policy, persons)], fetched, [room_key])
    await _stream(watched, start="2026-09-28")
    assert fetched.of(room_key).messages == {MESSAGE_ID: "Ada"}


async def test_a_tag_turns_record_query_prints_no_ids(policy, persons):
    """R-18: a number is a handle, and a tag turn never carries one."""
    answer = await _stream(_tag_tool(policy, persons), start="2026-09-28")
    assert answer["status"] == "ok", answer
    [row] = answer["messages"]
    assert "n" not in row
    assert str(MESSAGE_ID) not in json.dumps(answer)


