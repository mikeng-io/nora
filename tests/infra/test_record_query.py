"""`record_query`, against Postgres (design/cognition-loop/15-map.md §4,
10-tiers.md §3; migration step 2).

The store: an interval, one author, words by pg_trgm on Cantonese and English
(design/dreamer-agent-loop/24-embedding-seam.md §2), each hit with its edit,
reactions and delete. The tool: which rooms a turn may read is lineage's
answer for the turn's room, decided before the read; a name resolves only in
this room (R-21); every failure is `unavailable`, never an empty answer
(R-27).

Rooms, people and messages are placeholders; the Cantonese lines are authored
here, not taken from any room.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.record_query import RecordQuery
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.record_query import SqlRecordQuery
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender

NOW = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)          # 12:00 HKT
DAY = 86_400_000
T0 = int(NOW.timestamp() * 1000) - 3 * DAY           # three days ago


def _hk(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Hong_Kong")
    ).strftime("%Y-%m-%d %H:%M")


# Room A lists three people; room B (same unit) two of them; room C (same
# unit) lists `*`; room D is another unit.
_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "ROOM-A"
label = "Big room"
persons = ["p1", "p2", "p3"]
unit = "u1"

[[conversations]]
platform = "discord"
id = "ROOM-B"
label = "Small room"
persons = ["p1", "p2"]
unit = "u1"

[[conversations]]
platform = "whatsapp"
id = "ROOM-C"
label = "Open room"
persons = "*"
unit = "u1"

[[conversations]]
platform = "signal"
id = "ROOM-D"
label = "Other group"
persons = ["p1"]
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
'''

_UNREACHABLE_TRANSIT = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)


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


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _say(engine, *, platform: str, room: str, person: str | None, sender: str,
               ts: int, body: str, is_nora: bool = False) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(sa.text(
            "INSERT INTO messages (platform, conversation_id, conversation_type, "
            "sender_id, sender_name, ts, body, person_id, is_nora) VALUES "
            "(:p, :room, 'group', :sender, :sender, :ts, :body, :person, :nora) RETURNING id"
        ), {"p": platform, "room": room, "sender": sender, "ts": ts, "body": body,
            "person": person, "nora": int(is_nora)})).scalar_one()


@pytest.fixture
async def said(engine):
    """Three days of placeholder talk across the four rooms."""
    ids = {}
    ids["a_camp"] = await _say(engine, platform="signal", room="ROOM-A", person="p1",
                               sender="+85200000001", ts=T0 + 1_000,
                               body="我今日去咗大步行，好攰")
    ids["a_hotel"] = await _say(engine, platform="signal", room="ROOM-A", person="p2",
                                sender="+85200000002", ts=T0 + 2_000,
                                body="聽日book咗酒店未？")
    ids["a_eng"] = await _say(engine, platform="signal", room="ROOM-A", person="p3",
                              sender="+85200000003", ts=T0 + DAY,
                              body="we went for a big walk on Sunday")
    ids["a_other"] = await _say(engine, platform="signal", room="ROOM-A", person="p1",
                                sender="+85200000001", ts=T0 + DAY + 1_000,
                                body="機器學習好難")
    ids["b_line"] = await _say(engine, platform="discord", room="ROOM-B", person="p2",
                               sender="ben-discord", ts=T0 + 2 * DAY,
                               body="大步行下次再去")
    ids["c_line"] = await _say(engine, platform="whatsapp", room="ROOM-C", person="p3",
                               sender="cy-wa", ts=T0 + 2 * DAY + 1_000,
                               body="大步行 in the open room")
    ids["d_line"] = await _say(engine, platform="signal", room="ROOM-D", person="p1",
                               sender="+85200000001", ts=T0 + 2 * DAY + 2_000,
                               body="大步行 in another group")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO message_edit (message_id, ts, body) VALUES (:id, :ts, :b)"
        ), {"id": ids["a_hotel"], "ts": T0 + 3_000, "b": "聽日book咗酒店未呀？定係後日"})
        for sender, emoji, removed in (("s1", "👍", 0), ("s2", "👍", 0),
                                       ("s3", "❤", 0), ("s3", "❤", 1)):
            await conn.execute(sa.text(
                "INSERT INTO message_reaction (message_id, sender_id, emoji, removed, ts) "
                "VALUES (:id, :s, :e, :r, :ts)"
            ), {"id": ids["a_camp"], "s": sender, "e": emoji, "r": removed, "ts": T0 + 5_000})
        await conn.execute(sa.text(
            "INSERT INTO message_delete (message_id, sender_id, ts) VALUES (:id, 's', :ts)"
        ), {"id": ids["a_other"], "ts": T0 + DAY + 9_000})
    return ids


# ---- the store ---------------------------------------------------------------


def _q(**kw) -> RecordQuery:
    base = {"rooms": (("signal", "ROOM-A"),), "start_ms": T0, "end_ms": T0 + 3 * DAY}
    return RecordQuery(**(base | kw))


async def test_the_interval_and_the_rooms_bound_the_read(engine, said):
    hits, more, _ = await SqlRecordQuery(engine).query(_q(end_ms=T0 + DAY))
    assert [h.row.id for h in hits] == [said["a_camp"], said["a_hotel"]]
    assert not more
    hits, more, _ = await SqlRecordQuery(engine).query(_q(limit=2))
    assert len(hits) == 2 and more
    # oldest first, of the newest two
    assert [h.row.id for h in hits] == [said["a_eng"], said["a_other"]]


async def test_an_author_keeps_only_their_messages(engine, said):
    hits, _, _ = await SqlRecordQuery(engine).query(_q(person_id="p1"))
    assert {h.row.id for h in hits} == {said["a_camp"], said["a_other"]}


async def test_cantonese_words_inside_a_sentence_are_found_and_unrelated_ones_are_not(
    engine, said,
):
    """24 §2: a three-character term inside a sentence, and a two-character
    one (below a full trigram), both found; «機器學習» shares no trigram with
    «大步行» and is not."""
    store = SqlRecordQuery(engine)
    hits, _, near = await store.query(_q(text="大步行"))
    assert [h.row.id for h in hits] == [said["a_camp"]]
    assert not near
    hits, _, _ = await store.query(_q(text="酒店"))
    assert [h.row.id for h in hits] == [said["a_hotel"]]
    hits, _, _ = await store.query(_q(text="walk"))
    assert [h.row.id for h in hits] == [said["a_eng"]]


async def test_a_near_match_is_marked_near(engine, said):
    hits, _, near = await SqlRecordQuery(engine).query(_q(text="big walks"))
    assert said["a_eng"] in {h.row.id for h in hits}
    assert said["a_eng"] in near


async def test_an_edit_is_folded_in_and_searchable(engine, said):
    hits, _, _ = await SqlRecordQuery(engine).query(_q(text="後日"))
    assert [h.row.id for h in hits] == [said["a_hotel"]]
    assert hits[0].row.body.endswith("後日")
    assert hits[0].row.edit is not None and hits[0].row.edit.was == "聽日book咗酒店未？"


async def test_reactions_count_what_stands_and_a_delete_is_marked(engine, said):
    hits, _, _ = await SqlRecordQuery(engine).query(_q())
    by_id = {h.row.id: h for h in hits}
    assert by_id[said["a_camp"]].reactions == (("👍", 2),)
    assert by_id[said["a_other"]].deleted
    assert not by_id[said["a_camp"]].deleted


# ---- the tool ----------------------------------------------------------------


def _tools(engine, policy, persons, *, room_id="ROOM-B", platform="discord",
           roster=None, record=True, lineage=True):
    room = Room(
        platform=platform, conversation_id=room_id,
        roster_source=roster, persons=persons,
    )
    tools = build_tools(
        store=FakeRecordStore(), conversation_id=room_id, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        clock=lambda: NOW,
        record=SqlRecordQuery(engine) if record else None,
        lineage=LineagePolicy.from_policy(policy) if lineage else None,
    )
    return {t.tool_name: t for t in tools}["record_query"]


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


START = _hk(T0 - 60_000)


async def test_all_rooms_reads_only_the_rooms_whose_lineage_passes_here(
    engine, said, policy, persons,
):
    """From room B (p1, p2): room A (p1..p3) is a superset, so its words may
    be shown here; room C lists `*`, room D is another unit. Neither is read,
    and the result says rooms were left out."""
    result = await _call(_tools(engine, policy, persons), start=START,
                         scope="all_rooms", text="大步行")
    assert result["status"] == "ok"
    rooms = {m["room"] for m in result["messages"]}
    assert rooms == {"Big room", "Small room"}
    text = json.dumps(result, ensure_ascii=False)
    assert "open room" not in text and "another group" not in text
    assert "were not searched" in result["note"]
    assert result["untrusted"] is True


async def test_from_the_wider_room_the_smaller_rooms_words_are_not_read(
    engine, said, policy, persons,
):
    """The same query from room A: room B's people are fewer than A's, so a
    line said in B is not shown to A (DEC-225)."""
    result = await _call(
        _tools(engine, policy, persons, room_id="ROOM-A", platform="signal"),
        start=START, scope="all_rooms", text="大步行",
    )
    assert {m["room"] for m in result["messages"]} == {"Big room"}


async def test_this_room_reads_this_room_only(engine, said, policy, persons):
    result = await _call(_tools(engine, policy, persons), start=START, text="大步行")
    assert [m["room"] for m in result["messages"]] == ["Small room"]
    assert "note" not in result


async def test_a_deleted_message_keeps_its_place_and_loses_its_words(
    engine, said, policy, persons,
):
    result = await _call(
        _tools(engine, policy, persons, room_id="ROOM-A", platform="signal"), start=START,
    )
    deleted = [m for m in result["messages"] if m.get("deleted")]
    assert len(deleted) == 1 and deleted[0]["body"] is None
    assert "機器學習" not in json.dumps(result, ensure_ascii=False)
    assert "deleted" in result["note"]
    camp = next(m for m in result["messages"] if "大步行" in (m["body"] or ""))
    assert camp["reactions"] == {"👍": 2}


async def test_an_author_resolves_only_in_this_room(engine, said, policy, persons):
    roster = FakeRosterSource(groups={
        "ROOM-A": (frozenset({"+85200000001"}), frozenset({"+85200000003"})),
    })
    tool = _tools(engine, policy, persons, room_id="ROOM-A", platform="signal",
                  roster=roster)
    result = await _call(tool, start=START, author="Ada")
    assert result["status"] == "ok"
    assert {m["sender"] for m in result["messages"]} == {"Ada"}
    # Ben is a person, but not in this room: indistinguishable from nobody.
    assert (await _call(tool, start=START, author="Ben"))["status"] == "nothing_found"
    assert (await _call(tool, start=START, author="Nobody"))["status"] == "nothing_found"


async def test_nothing_in_the_stretch_is_nothing_found_not_never_said(
    engine, said, policy, persons,
):
    result = await _call(_tools(engine, policy, persons), start=_hk(T0 + 2 * DAY + 60_000),
                         text="大步行")
    assert result["status"] == "nothing_found"
    assert "not that it was never said" in result["note"]


@pytest.mark.parametrize("kwargs, why", [
    ({"record": False}, "no store wired"),
    ({"lineage": False}, "no lineage policy wired"),
    ({"room_id": "NOT-A-ROOM"}, "a room outside the policy"),
])
async def test_what_it_cannot_check_is_unavailable(engine, said, policy, persons, kwargs, why):
    result = await _call(_tools(engine, policy, persons, **kwargs), start=START)
    assert result["status"] == "unavailable", why


async def test_an_unreadable_time_is_unavailable_and_never_a_silent_read(
    engine, said, policy, persons,
):
    result = await _call(_tools(engine, policy, persons), start="last Saturday")
    assert result["status"] == "unavailable"
    assert "messages" not in result


async def test_the_trigram_index_can_serve_both_operators(engine, said):
    """The cost side (AGENTS.md: an instrument measures the result, something
    must measure the cost): with a sequential scan priced out, the planner
    reaches `idx_messages_body_trgm` for the substring and the word-similarity
    operator alike. At today's size it may still choose a scan, which is
    correct; what must hold is that the index is reachable."""
    async with engine.connect() as conn:
        await conn.execute(sa.text("SET enable_seqscan = off"))
        for predicate in ("body ILIKE '%大步行%'", "'大步行' <% body"):
            plan = "\n".join(r[0] for r in await conn.execute(sa.text(
                f"EXPLAIN SELECT id FROM messages WHERE {predicate}"  # noqa: S608 — literal test predicates
            )))
            assert "idx_messages_body_trgm" in plan, (predicate, plan)
