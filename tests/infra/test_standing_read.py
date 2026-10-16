"""`standing_read` and `SqlStandingStore.read_head`, against Postgres.

The store: the head with the id of the `standing_revision` row that IS it, and
no id at all when the chain does not end at the head (never another
position's). The tool: a room's Standing, withdrawal-aware (an unsupported head
is not returned), lineage per section (a section that fails is withheld and the
rest is shown), a surprise withholds every section, and every failure is
`unavailable`, never an empty answer (R-27).

What each test would catch if its behaviour were reverted is named in
`scripts/mutations/cognition-reads.json`. Rooms, people and words are
placeholders.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import BODY, HOLD_OPEN, I_OWE, WORKING_SET, Lineage, LineageEntry
from nora.domain.standing import Standing, WorkingSetItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import STANDING, STANDING_REVISION
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender

NOW = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)
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
'''

_IDENTITY = '''
[persons.p1]
name = "Ada"
[[persons.p1.signal]]
phone = "+85200000001"
[[persons.p1.discord]]
user_id = "1001"

[persons.p2]
name = "Ben"
[[persons.p2.signal]]
phone = "+85200000002"
'''

_UNREACHABLE = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)
_A_ROSTER = FakeRosterSource(groups={"ROOM-A": (frozenset({"+85200000001"}),
                                                frozenset({"+85200000002"}))})


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _standing(**kw) -> Standing:
    base = {
        "platform": "signal", "conversation_id": "ROOM-A", "body": "They plan a dinner.",
        "fold_count": 1, "updated_at": T, "hold_open": "whether it is Friday",
        "i_owe": "a reminder about the booking",
        "working_set": (WorkingSetItem(what="the restaurant is closed Mondays",
                                       source="web_search", found_at=T, bears_on="the dinner"),),
    }
    return Standing(**(base | kw))


def _tool(engine, tmp_path, *, room_id="ROOM-A", platform="signal", roster=_A_ROSTER,
          wired=True, lineage=True, metrics=None):
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    policy = load_policy(tmp_path / "policy.toml")
    persons = load_persons(tmp_path / "identity.toml")
    room = Room(platform=platform, conversation_id=room_id, roster_source=roster,
                persons=persons)
    tools = build_tools(
        store=FakeRecordStore(), conversation_id=room_id, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=room, transit_source=_UNREACHABLE,
        clock=lambda: NOW,
        standing_head=SqlStandingStore(engine) if wired else None,
        lineage=LineagePolicy.from_policy(policy) if lineage else None,
    )
    return {t.tool_name: t for t in tools}["standing_read"]


async def _call(tool):
    return await tool._tool_func()


async def _revision_ids(engine) -> list[str]:
    async with engine.connect() as conn:
        return [str(r[0]) for r in await conn.execute(
            sa.select(STANDING_REVISION.c.id).order_by(STANDING_REVISION.c.updated_at,
                                                       STANDING_REVISION.c.fold_count))]


# ---- the store: the head and the revision that IS it ---------------------------------------------

async def test_the_head_comes_with_the_id_of_the_revision_that_is_it(engine):
    store = SqlStandingStore(engine)
    await store.write(standing=_standing(fold_count=1, body="First."))
    await store.write(standing=_standing(fold_count=2, updated_at=T + 1_000, body="Second."))
    head = await store.read_head(platform="signal", conversation_id="ROOM-A")
    assert head is not None and head.standing.body == "Second."
    older, newest = await _revision_ids(engine)
    assert head.revision_id == newest != older


async def test_a_room_with_no_head_has_no_head(engine):
    assert await SqlStandingStore(engine).read_head(
        platform="signal", conversation_id="NOPE") is None


async def test_a_chain_that_does_not_end_at_the_head_gives_no_id_never_another_positions(engine):
    """A head edited outside `write` (or a room whose chain predates
    `standing_revision`): the newest revision is not this position, so it is
    not offered as the token a revision would be checked against."""
    store = SqlStandingStore(engine)
    await store.write(standing=_standing(body="Chain."))
    async with engine.begin() as conn:
        await conn.execute(sa.update(STANDING).values(body="Edited by hand."))
    head = await store.read_head(platform="signal", conversation_id="ROOM-A")
    assert head is not None and head.standing.body == "Edited by hand."
    assert head.revision_id is None
    async with engine.begin() as conn:
        await conn.execute(sa.delete(STANDING_REVISION))
    assert (await store.read_head(platform="signal", conversation_id="ROOM-A")).revision_id is None


async def test_reading_the_head_writes_nothing(engine):
    store = SqlStandingStore(engine)
    await store.write(standing=_standing())
    async with engine.connect() as conn:
        before = [tuple(r) for r in await conn.execute(sa.text(
            "SELECT 's', count(*), max(updated_at), max(fold_count) FROM standing UNION ALL "
            "SELECT 'r', count(*), max(updated_at), max(fold_count) FROM standing_revision"))]
    await store.read_head(platform="signal", conversation_id="ROOM-A")
    async with engine.connect() as conn:
        after = [tuple(r) for r in await conn.execute(sa.text(
            "SELECT 's', count(*), max(updated_at), max(fold_count) FROM standing UNION ALL "
            "SELECT 'r', count(*), max(updated_at), max(fold_count) FROM standing_revision"))]
    assert after == before


# ---- the tool -----------------------------------------------------------------------------------

async def test_the_tool_shows_the_sections_and_the_revision_it_stands_at(engine, tmp_path):
    await SqlStandingStore(engine).write(standing=_standing())
    (revision,) = await _revision_ids(engine)
    result = await _call(_tool(engine, tmp_path))
    assert result["status"] == "ok"
    assert result["revision"] == revision
    assert result["where_it_stands"] == "They plan a dinner."
    assert result["hold_open"] == "whether it is Friday"
    assert result["i_owe"] == "a reminder about the booking"
    (item,) = result["found_not_yet_given"]
    assert item["what"] == "the restaurant is closed Mondays" and item["bears_on"] == "the dinner"
    assert item["found"].startswith("2026-09-30T")            # a time she can read
    assert "note" not in result


async def test_a_head_with_no_revision_omits_the_key_not_null(engine, tmp_path):
    """DEC-246: `revision` is ABSENT when no revision IS the head; a null would
    read as a token."""
    await SqlStandingStore(engine).write(standing=_standing())
    async with engine.begin() as conn:
        await conn.execute(sa.delete(STANDING_REVISION))
    result = await _call(_tool(engine, tmp_path))
    assert result["status"] == "ok" and result["where_it_stands"] == "They plan a dinner."
    assert "revision" not in result


async def test_a_room_that_never_folded_is_nothing_found_not_an_error(engine, tmp_path):
    result = await _call(_tool(engine, tmp_path))
    assert result["status"] == "nothing_found" and "No supported position" in result["note"]


async def test_a_section_that_fails_lineage_is_withheld_and_the_rest_is_shown(engine, tmp_path):
    """Per section: `I hold open` rests on a `presence` entry, and room
    A is not location-trusted, so that section is not shown; the others are."""
    await SqlStandingStore(engine).write(standing=_standing())
    lineage = Lineage.of({
        BODY: [LineageEntry("message", None, "signal:ROOM-A")],
        HOLD_OPEN: [LineageEntry("presence", "p1", None)],
        I_OWE: [LineageEntry("message", None, "signal:ROOM-A")],
        WORKING_SET: [LineageEntry("message", None, "signal:ROOM-A")],
    })
    async with engine.begin() as conn:
        await conn.execute(sa.update(STANDING).values(lineage=lineage.to_json()))
    result = await _call(_tool(engine, tmp_path))
    assert result["status"] == "ok"
    assert "hold_open" not in result and "Friday" not in json.dumps(result)
    assert result["where_it_stands"] and result["i_owe"] and result["found_not_yet_given"]
    assert "Some sections are not shown" in result["note"]


async def test_a_surprise_withholds_every_section_at_once(engine, tmp_path):
    await SqlStandingStore(engine).write(standing=_standing(exclude_persons=("p2",)))
    result = await _call(_tool(engine, tmp_path))          # Ben is in this room
    assert result["status"] == "nothing_found"
    assert "Some sections are not shown" in result["note"]
    assert "dinner" not in json.dumps(result)


async def test_a_head_of_another_room_is_not_this_rooms(engine, tmp_path):
    """The tool reads the Standing of the room it is bound to and takes no
    room argument: room B cannot be pointed at room A's position."""
    await SqlStandingStore(engine).write(standing=_standing())
    tool = _tool(engine, tmp_path, room_id="ROOM-B", platform="discord",
                 roster=FakeRosterSource(groups={}))
    result = await _call(tool)
    assert result["status"] == "nothing_found"
    assert "dinner" not in json.dumps(result)
    assert not inspect.signature(tool._tool_func).parameters


@pytest.mark.parametrize("kwargs, why", [
    ({"wired": False}, "no Standing read wired"),
    ({"lineage": False}, "no lineage policy wired"),
    ({"room_id": "NOT-A-ROOM"}, "a room outside the policy"),
])
async def test_what_it_cannot_check_is_unavailable(engine, tmp_path, kwargs, why):
    await SqlStandingStore(engine).write(standing=_standing())
    result = await _call(_tool(engine, tmp_path, **kwargs))
    assert result["status"] == "unavailable", why
    assert "dinner" not in json.dumps(result)


async def test_a_failing_store_is_unavailable_not_nothing_found(engine, tmp_path):
    tool = _tool(engine, tmp_path)
    with patch.object(SqlStandingStore, "read_head", side_effect=RuntimeError("down")):
        result = await _call(tool)
    assert result["status"] == "unavailable"


async def test_every_call_is_counted_by_its_status(engine, tmp_path):
    metrics = Metrics()
    tool = _tool(engine, tmp_path, metrics=metrics)
    await _call(tool)                                        # never folded
    await SqlStandingStore(engine).write(standing=_standing())
    await _call(tool)
    assert metrics.get("nora_tool_calls_total", tool="standing_read", status="nothing_found") == 1
    assert metrics.get("nora_tool_calls_total", tool="standing_read", status="ok") == 1
