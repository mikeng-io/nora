"""A Cognition session's READ tools write nothing.

**The hazard.** A READ tool that wrote `note_candidate` or bumped
`nora_note_candidates_total` would be writing the table the live note curator
reads to decide a note should exist. Pinning «shadow writes nothing live» only
for the session's own write verbs, driven with `read_tools_for=lambda u, r: []`,
never runs a read tool. That is the class: not one tool but «the session holds
tools whose side effects nobody audited».

**What this runs.** Every read tool the session is wired with
(`COGNITION_READ_CATALOGUE` less what a flag withholds), built through the REAL
registry (`RegistryToolSource` + `cognition_grant()`, the composition's own
path) over REAL Postgres stores, called with arguments that reach the store
(a miss, a hit, an interval, a person), and then every table of the schema is
counted again: NOT ONE table outside `cognition_*` may have gained a row.
Honcho is a recording double that fails on any write verb.

A test that finds nothing proves nothing, so it has its own positive control:
a row written to a live table DOES move the snapshot, so it can see the write
it exists to catch.

Rooms, people and words are placeholders.

What each test would catch if its guard were removed is named in
`scripts/mutations/cognition-step3a3-review.json`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from nora.agent.dreamer import HomeRoom, RegistryToolSource, cognition_grant
from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.registry import ToolRegistry
from nora.agent.tools import GRAPH_MEMORY_TOOLS, HONCHO_MEMORY_TOOLS
from nora.agent.whereis import Circle
from nora.app.cognition_wiring import (
    COGNITION_ADAPTOR_NAME,
    read_surface,
    read_tools_for,
)
from nora.app.gatherings import GatheringService
from nora.domain.cognition_session import Room
from nora.domain.media_join import MediaJoin
from nora.domain.social_stream import MediaItem, SocialItem
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRosterSource

NOW_MS = 1_790_000_000_000
A = Room("signal", "ROOM-A")
B = Room("discord", "ROOM-B")
C = Room("whatsapp", "ROOM-C")               # in the unit, NOT on the agenda below
ADA, BEN = "+85200000001", "+85200000002"

_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "ROOM-A"
label = "Room A"
persons = ["p1", "p2"]
unit = "u1"
home_room = true

[[conversations]]
platform = "discord"
id = "ROOM-B"
label = "Room B"
persons = ["p1", "p2"]
unit = "u1"

[[conversations]]
platform = "whatsapp"
id = "ROOM-C"
label = "Room C"
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
[[persons.p2.discord]]
user_id = "1002"
'''


class ReadOnlyMemory(FakeMemoryStore):
    """Honcho as a session may use it: the read verbs answer, every write verb
    fails the test. `FakeMemoryStore` records what was written; this raises."""

    async def feed(self, **kw: Any) -> bool:
        raise AssertionError("a session read tool fed Honcho")

    async def conclude(self, **kw: Any) -> Any:
        raise AssertionError("a session read tool wrote a Honcho conclusion")

    async def ensure_peer(self, **kw: Any) -> None:
        raise AssertionError("a session read tool created a Honcho peer")


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _counts(engine) -> dict[str, int]:
    """Every base table of the schema and its row count. Not a sample of the
    tables a tool is known to touch: the whole schema, so a write to a table
    nobody thought of is still seen."""
    async with engine.connect() as conn:
        tables = [r[0] for r in await conn.execute(sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
        ))]
        return {
            t: (await conn.execute(
                sa.text(f'SELECT count(*) FROM "{t}"'),  # noqa: S608 (names from information_schema)
            )).scalar_one()
            for t in tables
        }


def _live(counts: dict[str, int]) -> dict[str, int]:
    """Everything a session may not touch: every table that is not its own."""
    return {t: n for t, n in counts.items() if not t.startswith("cognition_")}


async def _say(engine, *, platform: str, room: str, person: str, sender: str, ts: int,
               body: str) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(sa.text(
            "INSERT INTO messages (platform, conversation_id, conversation_type, sender_id, "
            "sender_name, ts, body, person_id, is_nora) VALUES "
            "(:p, :room, 'group', :sender, :sender, :ts, :body, :person, 0) RETURNING id"
        ), {"p": platform, "room": room, "sender": sender, "ts": ts, "body": body,
            "person": person})).scalar_one()


class World:
    """The real registry and tool source over real stores, and the doubles."""

    def __init__(self, engine, tmp_path, *, graph: bool = False) -> None:
        (tmp_path / "policy.toml").write_text(_POLICY)
        (tmp_path / "identity.toml").write_text(_IDENTITY)
        self.policy = load_policy(tmp_path / "policy.toml")
        self.persons = load_persons(tmp_path / "identity.toml")
        self.metrics = Metrics()
        self.memory = ReadOnlyMemory(
            cards={"p1": ["Ada plans the dinners"]},
            conclusions={"p1": ["Ada likes ramen"]},
        )
        self.memory.messages.append(("p1", "dinner is on friday"))
        self.notes = SqlNoteStore(engine)
        self.memos = SqlMemoStore(engine)
        self.memo_id = ""
        media_join = MediaJoin(
            store=SqlMediaObjectStore(engine), screen=lambda text: None, metrics=self.metrics,
        )
        events = GatheringService(
            store=SqlGatheringStore(engine), route_source=None, honcho=self.memory,
            ai_peer="nora",
        )
        circle = Circle(
            circle_id="c1", person_ids=frozenset({"p1", "p2"}),
            people=tuple(self.persons.by_person[p] for p in ("p1", "p2")),
        )
        registry = ToolRegistry(
            content_checks=not graph,
            record=SqlRecordQuery(engine),
            # The two Dreamer-only reads: a `rollup_read` that started a build or a
            # `standing_read` that folded would be a write this test must see.
            rollup=SqlRollupRead(engine), standing_head=SqlStandingStore(engine),
            # `social_read`: a read that perceived or fetched an item would be
            # a write this test must see.
            social_items=SqlSocialStreamStore(engine),
            lineage=LineagePolicy.from_policy(self.policy, circles=(circle,), mode="on"),
            notes=self.notes, memos=self.memos,
            # The memory graph's surface (DEC-316): `memory_search` over the
            # real index read, and no Honcho memory verb.
            memory_search=(
                MemorySearch(SqlMemoryIndex(engine), self.metrics, graph=SqlMemoryGraph(engine))
                if graph else None
            ),
            note_unit=lambda platform, cid: "u1", unit_for=lambda platform, cid: "u1",
            events=events, mobility=SqlMobilityStore(engine), circles=(circle,),
            media_join=media_join,
            # Wired on purpose: the recall log is a table, and a read tool that
            # logged its own call to it would be a write this test must see.
            tool_call_log_store=SqlToolCallLogStore(engine),
            clock=lambda: __import__("datetime").datetime.fromtimestamp(
                NOW_MS / 1000, tz=__import__("datetime").UTC),
        )
        rooms = {
            cid: HomeRoom(unit=conv.unit, platform=conv.platform, conversation_id=conv.primary_id,
                          conversation_type="group", location_trusted=True)
            for conv in self.policy.conversations for cid in conv.ids
        }
        members = (frozenset({ADA}), frozenset({BEN}))
        self.source = RegistryToolSource(
            registry=registry, store=SqlRecordStore(engine), persons=self.persons,
            honcho=self.memory, metrics=self.metrics, ai_peer="nora",
            home_rooms={"u1": rooms["ROOM-A"]}, scope_rooms=rooms,
            rosters={
                "signal": FakeRosterSource(groups={"ROOM-A": members}),
                "discord": FakeRosterSource(groups={"ROOM-B": members}),
                "whatsapp": FakeRosterSource(groups={"ROOM-C": (frozenset({ADA}),)}),
            },
        )
        self.surface = read_surface(
            read_only=self.source.read_only(), enabled_flags={"record_query.enabled"},
            registry_withheld=registry.withheld_tools(),
        )

    def tools(self, rooms: list[Room]) -> dict[str, Any]:
        built = read_tools_for(self.source, names=self.surface.wired)("u1", rooms)
        return {t.tool_name: t for t in built}


async def _call(tool: Any, **arguments: Any) -> dict[str, Any]:
    events = [e async for e in tool.stream(
        {"toolUseId": "t", "name": tool.tool_name, "input": arguments}, {},
    )]
    text = events[-1].tool_result["content"][0]["text"]
    return json.loads(text)


#: One call per wired tool, with arguments that REACH its store: a miss and a
#: hit for the note search, an interval that holds messages, a real name.
CALLS: dict[str, list[dict[str, Any]]] = {
    "note_search": [{"query": "zzz-no-such-word"}, {"query": "Tokyo"}],
    "note_read": [{"title": "Trip"}, {"title": "Nothing Like This"}],
    # `MEMO` is the seeded memo's id, filled in at call time (DEC-306).
    "memo_read": [{"memo_id": "MEMO"}, {"memo_id": "00000000-0000-4000-8000-000000000000"}],
    "record_query": [{"start": "2026-09-01", "end": "2026-10-30"},
                     {"start": "2026-09-01", "end": "2026-10-30", "text": "dinner",
                      "scope": "all_rooms"}],
    "event_search": [{}, {"place": "ramen"}],
    "rollup_read": [
        {"kind": "daybook", "subject": "Ada", "interval": "2026-09-01/2026-10-30"},
        {"kind": "tie", "subject": "Ada, Ben", "interval": "2026-09-01"},
        {"kind": "stay", "subject": "everyone", "interval": "2026-09-01/2026-10-30"},
    ],
    "standing_read": [{}],
    "social_read": [{"person": "Ada", "interval": "2026-09-01/2026-10-30"},
                    {"person": "Ben", "interval": "2026-09-01"}],
    "where_been": [{"what": "places"}, {"what": "day", "date": "2026-09-30"}],
    "routine_read": [{"date": "2026-10-01", "from_hour": 18, "to_hour": 21}],
    "about": [{"person": "Ada"}],
    "ask_memory": [{"person": "Ada"}],
    "search_memory": [{"query": "dinner"}],
    "search_memory_workspace": [{"query": "dinner"}],
    # The graph surface's (DEC-316): a plain search, and one narrowed every
    # way it can be, so each filter's SQL runs.
    "memory_read": [{"kind": "message", "id": "MEMORY", "depth": 0}],
    "memory_search": [
        {"query": "dinner"},
        {"query": "dinner", "person": "Ada", "since": "2026-09-01", "until": "2026-10-30",
         "kinds": ["messages", "places"]},
    ],
}


@pytest.fixture(params=[False, True], ids=["honcho_surface", "graph_surface"])
async def world(request, engine, tmp_path):
    w = World(engine, tmp_path, graph=request.param)
    async with engine.begin() as conn:
        # The Rollup builder's row, so the derived-presence reads reach their
        # SQL instead of answering «no build is current yet».
        await conn.execute(sa.text(
            "INSERT INTO rollup_build (code_version, params_sha256, status) "
            "VALUES ('test', 'test', 'current')"
        ))
    await w.notes.create(
        unit="u1", title="Trip", description="the Tokyo trip", uncertainty="who flies",
        closing_condition="flights booked", established_in="ROOM-A",
        body="## Open\n- flights to Tokyo [1]\n", established_by="tag:test", now_ms=NOW_MS,
        anchor_date=20261001,
    )
    memo = await w.memos.create(
        unit="u1", platform="signal", conversation_id="ROOM-A", title="wifi",
        body="## Kept\n- the wifi password is hunter-two [1]", citations=("1",),
        mode="told", established_by="cognition:u1", model=None, now_ms=NOW_MS,
    )
    w.memo_id = memo.id
    # One recorded post of Ada's, so `social_read` reaches its SQL with a hit.
    taken = datetime.fromtimestamp(NOW_MS / 1000 - 86_400, tz=UTC)
    await SqlSocialStreamStore(engine).insert_item(
        SocialItem(network="instagram", item_id="1", account="acct-1", kind="post",
                   taken_at=taken, expires_at=None, caption="dinner was good",
                   alt_text=None, media=(MediaItem("image", "https://x"),)),
        person="p1", origin="poll", seen_at=taken,
    )
    for i, (room, who, sender) in enumerate((
        (A, "p1", ADA), (A, "p2", BEN), (B, "p1", ADA),
    )):
        mid = await _say(engine, platform=room.platform, room=room.conversation_id, person=who,
                   sender=sender, ts=NOW_MS - 3 * 86_400_000 + i * 60_000,
                   body=f"dinner plans number {i}")
        if i == 0:
            w.memory_id = str(mid)
        if request.param:
            from nora.domain.memory_graph import Relation, pointer_entity
            from nora.infra.sql.schema import MEMORY_NODE
            async with engine.begin() as conn:
                await conn.execute(sa.insert(MEMORY_NODE).values(
                    kind="message", source_id=str(mid), text=f"dinner plans number {i}",
                    at_ms=NOW_MS - 3 * 86_400_000 + i * 60_000, unit="u1",
                    origin_room=f"{room.platform}:{room.conversation_id}", persons=[who],
                    exclude_persons=[], lineage=[]))
            pointer = pointer_entity("room", "u1", f"{room.platform}:{room.conversation_id}",
                                     room.conversation_id)
            graph = SqlMemoryGraph(engine)
            admitted = await graph.stage_and_admit(entities=(pointer,), relations=(Relation(
                f"generated-read-{mid}", ("message", str(mid)), "in_room", pointer.key,
                "record", "u1", (who,), asserted_at=datetime.fromtimestamp(
                    (NOW_MS - 3 * 86_400_000 + i * 60_000) / 1000, UTC)),),
                evidence=(), sources={})
            assert admitted.relations
    return w


# ---- the class: no read tool the session holds writes anything --------------------------------

async def test_the_call_table_covers_every_wired_tool(world):
    """The audit is only as good as what it ran: a wired tool with no call here
    would be «checked» by not being called."""
    assert set(world.surface.wired) <= set(CALLS)
    # what the table holds beyond this surface is the OTHER memory surface's
    assert set(CALLS) - set(world.surface.wired) <= HONCHO_MEMORY_TOOLS | GRAPH_MEMORY_TOOLS


async def test_every_read_tool_the_session_holds_writes_nothing_live(engine, world):
    before = await _counts(engine)
    tools = world.tools([A])
    assert set(tools) == set(world.surface.wired)
    results: dict[str, list[str]] = {}
    for name in sorted(world.surface.wired):
        for arguments in CALLS[name]:
            # Independent coverage probes, each with its own turn's read budget/cache.
            tools = world.tools([A])
            answer = await _call(tools[name], **{
                k: world.memo_id if v == "MEMO" else world.memory_id if v == "MEMORY" else v
                for k, v in arguments.items()
            })
            results.setdefault(name, []).append(str(answer.get("status")))
    after = await _counts(engine)

    assert _live(after) == _live(before), {
        t: (before.get(t), n) for t, n in _live(after).items() if before.get(t) != n
    }
    # A tool that answered `unavailable` for a wiring gap was never really run.
    reached = {"note_search", "note_read", "record_query", "event_search",
               "rollup_read", "standing_read", "memo_read", "social_read"}
    for name in reached:
        assert set(results[name]) <= {"ok", "nothing_found"}, (name, results[name])
    assert "nothing_found" in results["note_search"] and "ok" in results["note_search"]
    assert "ok" in results["record_query"] and "ok" in results["note_read"]
    assert results["memo_read"] == ["ok", "nothing_found"]
    assert results["social_read"] == ["ok", "nothing_found"]
    # the derived-presence reads reached their SQL (a current build exists)
    assert not {"unavailable"} & set(results["where_been"] + results["routine_read"]), results
    # No counter that feeds a decision moved, and Honcho was only ever read.
    assert world.memory.fed == [] and world.memory.writes == [] and world.memory.peers == []
    if "memory_search" in world.surface.wired:
        # The graph surface: the index answered, and Honcho was never read at
        # all: no Honcho tool sits on it, `about` included (DEC-316).
        assert not HONCHO_MEMORY_TOOLS & set(world.surface.wired)
        assert "unavailable" not in results["memory_search"], results["memory_search"]
        assert results["memory_read"] == ["ok"]
        assert world.memory.chats == [] and world.memory.searches == []
        assert world.memory.queries == []
    else:
        assert world.memory.chats and world.memory.searches
        assert world.memory.queries   # and `about` WAS read


async def test_the_positive_control_a_live_write_moves_the_snapshot(engine, world):
    """Without this the assertions above could pass because the snapshot cannot
    see a write. `note_search` records no miss anywhere (DEC-277), so
    the control is a row written to a live table directly: `_live` must move."""
    before = await _counts(engine)
    await _say(engine, platform="signal", room="ROOM-A", person="p1", sender=ADA,
               ts=NOW_MS, body="a control row")
    after = await _counts(engine)
    assert after["messages"] == before["messages"] + 1
    assert _live(after) != _live(before)


async def test_a_cognition_miss_records_nothing_and_still_answers_nothing_found(engine, world):
    (tool,) = world.source.build(
        key="ROOM-A", names=frozenset({"note_search"}), unit=None,
        adaptor=COGNITION_ADAPTOR_NAME, grant=cognition_grant(),
    )
    before = (await _counts(engine))["note_candidate"]
    answer = await _call(tool, query="zzz-no-such-word")
    assert answer["status"] == "nothing_found"                  # the read is unchanged
    assert (await _counts(engine))["note_candidate"] == before


# ---- a two-room agenda reads each room through itself, through the real registry ----------------

async def test_a_two_room_agenda_reads_each_room_through_itself_and_cannot_read_another(
    engine, world,
):
    tools = world.tools([A, B])
    record = tools["record_query"]
    interval = {"start": "2026-09-01", "end": "2026-10-30"}
    in_a = await _call(record, room="signal:ROOM-A", **interval)
    in_b = await _call(record, room="discord:ROOM-B", **interval)
    assert in_a["status"] == in_b["status"] == "ok"
    assert {m["body"] for m in in_a["messages"]} == {"dinner plans number 0",
                                                     "dinner plans number 1"}
    assert {m["body"] for m in in_b["messages"]} == {"dinner plans number 2"}
    # A room of the unit that is not on THIS agenda cannot be read, and the
    # unit's home room is not a fallback for a missing `room`.
    refused = await _call(record, room="whatsapp:ROOM-C", **interval)
    assert refused["status"] == "refused" and refused["reason"] == "unknown_room"
    missing = await _call(record, **interval)
    assert missing["status"] == "refused" and missing["reason"] == "room_required"


async def test_the_two_room_reads_write_nothing_either(engine, world):
    before = await _counts(engine)
    tools = world.tools([A, B])
    for name in sorted(world.surface.wired):
        for arguments in CALLS[name]:
            for room in ("signal:ROOM-A", "discord:ROOM-B"):
                await _call(tools[name], room=room, **arguments)
    assert _live(await _counts(engine)) == _live(before)
