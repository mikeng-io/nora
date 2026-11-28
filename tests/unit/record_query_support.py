"""A recorded room, its policy and identity fixtures, and a tool-stream reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.record_query import RecordHit
from nora.persons import load_persons
from nora.policy import load_policy

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
    """The Record read, with one recorded message in the room. `last_as_of`
    keeps the bound the last call was made under, so a test can see the read
    was really bounded rather than merely offered a clock."""

    last_as_of: int | str | None = "unset"

    async def query(
        self, q: Any, *, as_of_ms: int | None = None,
    ) -> tuple[tuple[RecordHit, ...], bool, frozenset[int]]:
        type(self).last_as_of = as_of_ms
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
