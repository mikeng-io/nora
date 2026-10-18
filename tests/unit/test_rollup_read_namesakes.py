"""`rollup_read` with two people in one room who share a name.

`ambiguous` must offer each of them by something that, named again, resolves
to that person alone (`nora.agent.room.tell_apart`): their full name or an
alias when one is theirs alone. In a Cognition session, a person with no such
name is offered by their person id, which the tool then accepts; on a tag
turn no tool hands the model a person id (instagram-adoption/18), so the
answer is a bare `ambiguous` and she asks. Two identical names in `could_be`
give the model nothing to ask with.

The store is a fake that records the subjects it was asked for: the
resolution happens before any read. Rooms and people are placeholders.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room, tell_apart
from nora.agent.tools import build_tools
from nora.domain.cognition_session import COGNITION_ADAPTOR
from nora.domain.rollup_read import RollupRead, RollupReadQuery
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender

NOW = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)

_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "group-1"
label = "Room"
persons = ["a", "b", "c"]
unit = "friends"
'''

# Two of them are called Ada: one has a last name, the other nothing else.
_IDENTITY = '''
[persons.a]
name = "Ada"
[[persons.a.signal]]
phone = "+85200000001"

[persons.b]
name = "Ada"
first_name = "Ada"
last_name = "Wong"
[[persons.b.signal]]
phone = "+85200000002"

[persons.c]
name = "Ben"
aliases = ["Benny"]
[[persons.c.signal]]
phone = "+85200000003"
'''

_UNREACHABLE = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)
_ROSTER = FakeRosterSource(groups={"group-1": (
    frozenset({"+85200000001"}), frozenset({"+85200000002"}), frozenset({"+85200000003"}),
)})


class _Rollup:
    def __init__(self) -> None:
        self.asked: list[tuple[str, ...]] = []

    async def read(self, q: RollupReadQuery) -> RollupRead:
        self.asked.append(q.subjects)
        return RollupRead(rows=(), more=False, build_id=None, through_ms=None)


def _world(tmp_path):
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    return load_policy(tmp_path / "policy.toml"), load_persons(tmp_path / "identity.toml")


def _tool(tmp_path, rollup: _Rollup, *, adaptor: str = ""):
    policy, persons = _world(tmp_path)
    room = Room(platform="signal", conversation_id="group-1", roster_source=_ROSTER,
                persons=persons)
    tools = build_tools(
        store=FakeRecordStore(), conversation_id="group-1", conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE, clock=lambda: NOW,
        rollup=rollup, lineage=LineagePolicy.from_policy(policy, circles=()),
        dream_adaptor=adaptor,
    )
    return {t.tool_name: t for t in tools}["rollup_read"]


async def _call(tool, subject: str) -> dict:
    return await tool._tool_func(kind="stay", subject=subject, interval="2026-09-21")


async def test_in_cognition_namesakes_are_offered_by_what_tells_them_apart(tmp_path):
    rollup = _Rollup()
    tool = _tool(tmp_path, rollup, adaptor=COGNITION_ADAPTOR)
    got = await _call(tool, "Ada")
    assert got["status"] == "ambiguous"
    assert got["could_be"] == ["a", "Ada Wong"]
    assert rollup.asked == []
    # Each entry, named again, reads that one person alone.
    await _call(tool, "a")
    await _call(tool, "Ada Wong")
    assert rollup.asked == [("a",), ("b",)]


async def test_on_a_tag_turn_no_person_id_is_offered_or_taken(tmp_path):
    rollup = _Rollup()
    tool = _tool(tmp_path, rollup)
    got = await _call(tool, "Ada")
    # One Ada has nothing but the name, so nothing is offered at all.
    assert got["status"] == "ambiguous" and "could_be" not in got
    assert "Ask which one." in got["note"]
    # An id is no name here: it resolves nobody.
    assert (await _call(tool, "a"))["status"] == "nothing_found"
    await _call(tool, "Ada Wong")
    assert rollup.asked == [("b",)]


async def test_an_alias_resolves_its_person(tmp_path):
    rollup = _Rollup()
    await _call(_tool(tmp_path, rollup), "Benny")
    assert rollup.asked == [("c",)]


def test_tell_apart_prefers_a_name_that_is_theirs_alone(tmp_path):
    _, persons = _world(tmp_path)
    people = list(persons.by_person.values())
    a, b, _c = people
    assert tell_apart([a, b], people, by_id=True) == ["a", "Ada Wong"]
    assert tell_apart([a, b], people, by_id=False) == [None, "Ada Wong"]
