"""`character_read`: the current account of a room or a person of the caller's
unit, in the R-27 three states.

The boundary is the unit, drawn by scope: a room or a person of another unit is
never a candidate, so it answers exactly like a name nobody uses and the store
is not asked. A person's account renders only for rooms that list them. Rooms,
people and words are placeholders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.culture import character_key
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource, InMemoryCultureStore
from tests.fakes import SinkSender

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
T = int(NOW.timestamp() * 1000) - 3_600_000

_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "ROOM-A"
label = "Room A"
persons = ["p1", "p2"]
unit = "u1"

[[conversations]]
platform = "discord"
id = "ROOM-B"
label = "Room B"
persons = ["p1"]
unit = "u1"

[[conversations]]
platform = "telegram"
id = "ROOM-S"
label = "Room Star"
persons = "*"
unit = "u1"

[[conversations]]
platform = "signal"
id = "ROOM-X"
label = "Room X"
persons = ["p1", "p3", "p4"]
unit = "u2"
'''

_IDENTITY = '''
[persons.p1]
name = "Ada"
[[persons.p1.signal]]
phone = "+85200000001"

[persons.p2]
name = "Ben"
[[persons.p2.signal]]
phone = "+85200000002"

[persons.p3]
name = "Cy"
[[persons.p3.signal]]
phone = "+85200000003"

[persons.p4]
name = "Ada"
[[persons.p4.signal]]
phone = "+85200000004"
'''

_UNREACHABLE = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)
_ROSTER = FakeRosterSource(groups={"ROOM-A": (frozenset({"+85200000001"}),
                                              frozenset({"+85200000002"}))})


def _tool(tmp_path, culture, *, lineage=True, metrics=None):
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    policy = load_policy(tmp_path / "policy.toml")
    persons = load_persons(tmp_path / "identity.toml")
    room = Room(platform="signal", conversation_id="ROOM-A", roster_source=_ROSTER,
                persons=persons)
    tools = build_tools(
        store=FakeRecordStore(), conversation_id="ROOM-A", conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=room, transit_source=_UNREACHABLE,
        clock=lambda: NOW, culture=culture,
        lineage=LineagePolicy.from_policy(policy) if lineage else None,
    )
    return {t.tool_name: t for t in tools}["character_read"]


async def _call(tool, entity, subject):
    return await tool._tool_func(entity=entity, subject=subject)


async def _seed(store, key, body, *, at=T):
    return await store.append(key=key, body=body, written_at=at, established_by="test")


async def test_a_rooms_account_comes_back_with_its_time_and_revision(tmp_path):
    store = InMemoryCultureStore()
    rev = await _seed(store, "ROOM-A", "They decide late and joke a lot.")
    got = await _call(_tool(tmp_path, store), "room", "room a")
    assert got["status"] == "ok" and got["entity"] == "room"
    assert got["accounts"] == [{
        "room": "Room A", "account": "They decide late and joke a lot.",
        "as_of": "2026-10-07T11:00:00+08:00", "revision": rev.id,
    }]
    assert store.reads == ["ROOM-A"]


async def test_a_persons_account_is_read_once_from_their_units_key(tmp_path):
    """One account per (unit, person): Ada is listed in two rooms of u1 and her
    account is read once, from the unit key; per-room keys and the other unit's
    are never read."""
    store = InMemoryCultureStore()
    await _seed(store, character_key("u1", "p1"), "Ada organises.")
    await _seed(store, character_key("ROOM-A", "p1"), "OLD PER-ROOM")
    await _seed(store, character_key("u2", "p1"), "OTHER UNIT")
    got = await _call(_tool(tmp_path, store), "person", "Ada")
    assert got["status"] == "ok"
    assert [a["account"] for a in got["accounts"]] == ["Ada organises."]
    assert store.reads == [character_key("u1", "p1")]


async def test_a_person_listed_in_one_room_is_read_from_the_unit_key(tmp_path):
    store = InMemoryCultureStore()
    await _seed(store, character_key("u1", "p2"), "Ben keeps score.")
    got = await _call(_tool(tmp_path, store), "person", "p2")     # an id works too
    assert [(a["room"], a["account"]) for a in got["accounts"]] == [
        ("Room A", "Ben keeps score.")]
    assert store.reads == [character_key("u1", "p2")]


async def test_no_account_is_nothing_found_not_unavailable(tmp_path):
    got = await _call(_tool(tmp_path, InMemoryCultureStore()), "room", "Room B")
    assert got["status"] == "nothing_found" and got["note"]


async def test_an_out_of_unit_room_or_person_reads_exactly_like_a_name_nobody_uses(tmp_path):
    store = InMemoryCultureStore()
    await _seed(store, "ROOM-X", "OTHER UNIT ROOM")
    await _seed(store, character_key("u2", "p3"), "OTHER UNIT PERSON")
    tool = _tool(tmp_path, store)
    nobody = await _call(tool, "room", "No Such Room")
    assert await _call(tool, "room", "Room X") == nobody
    assert await _call(tool, "person", "Cy") == await _call(tool, "person", "Nobody At All")
    assert (await _call(tool, "person", "Cy"))["status"] == "nothing_found"
    assert store.reads == []         # never a candidate, so never read


async def test_a_namesake_in_another_unit_neither_resolves_nor_makes_the_name_ambiguous(tmp_path):
    """p4 is also named Ada but listed only in the other unit: «Ada» here is p1."""
    store = InMemoryCultureStore()
    await _seed(store, character_key("u1", "p1"), "Ada organises.")
    got = await _call(_tool(tmp_path, store), "person", "Ada")
    assert got["status"] == "ok"
    assert [a["account"] for a in got["accounts"]] == ["Ada organises."]
    assert character_key("u2", "p4") not in store.reads


async def test_an_account_carrying_an_identifier_is_withheld_whole(tmp_path):
    store = InMemoryCultureStore()
    await _seed(store, "ROOM-A", "Call them on +85291234567 to sort it out.")
    got = await _call(_tool(tmp_path, store), "room", "Room A")
    assert got["status"] == "nothing_found" and got["withheld"] == 1
    assert "+852" not in str(got)


async def test_a_store_failure_is_unavailable_never_nothing_found(tmp_path):
    store = InMemoryCultureStore()
    metrics = Metrics()
    with patch.object(store, "head", side_effect=RuntimeError("down")):
        got = await _call(_tool(tmp_path, store, metrics=metrics), "room", "Room A")
    assert got["status"] == "unavailable" and got["note"]


@pytest.mark.parametrize("wired", ["culture", "lineage"])
async def test_an_unwired_read_is_unavailable(tmp_path, wired):
    tool = _tool(tmp_path, None if wired == "culture" else InMemoryCultureStore(),
                 lineage=wired != "lineage")
    assert (await _call(tool, "room", "Room A"))["status"] == "unavailable"


async def test_an_unknown_entity_is_unavailable(tmp_path):
    got = await _call(_tool(tmp_path, InMemoryCultureStore()), "unit", "Room A")
    assert got["status"] == "unavailable"
