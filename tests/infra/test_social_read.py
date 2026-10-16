"""`social_read` and `SqlSocialStreamStore.items_of`, against Postgres.

The read (design/cognition-loop/22-social-stream.md §6, 15 §4's
`social_items`): one person's recorded stories, posts and reels in an
interval, newest first. An item is shown only in a room that lists its author
(the `social` lineage predicate); a person who opted out (`social = false`)
and an item gone or hidden are never read; the location is shown only where
the room may place the author (the `presence` predicate: the room's
`location` flag AND the live DisclosureGuard); the answer is bounded and says
when it was cut; every item says what it is and how old it is.

Rooms, people, places and words are placeholders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.social_read import SOCIAL_READ_MAX
from nora.agent.tools import build_tools
from nora.agent.whereis import Circle
from nora.domain.social_stream import MediaItem, SocialItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
INTERVAL = "2026-10-01/2026-10-07"
ADA, BEN, CY, DEE = "+85200000001", "+85200000002", "+85200000003", "+85200000004"

#: One room per platform per unit (DEC-105), so each room is its own unit: the
#: read is scoped by who a room lists, never by its unit.
_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "ROOM-A"
label = "Room A"
persons = ["p1", "p2", "p3"]
unit = "u1"
location = true

[[conversations]]
platform = "signal"
id = "ROOM-B"
label = "Room B"
persons = ["p2"]
unit = "u2"
location = true

[[conversations]]
platform = "signal"
id = "ROOM-C"
label = "Room C"
persons = ["p1", "p2"]
unit = "u3"

[[conversations]]
platform = "signal"
id = "ROOM-D"
label = "Room D"
persons = ["p1", "p4"]
unit = "u4"
location = true

[[conversations]]
platform = "signal"
id = "ROOM-S"
label = "Room Star"
persons = "*"
unit = "u5"
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
social = false
[[persons.p3.signal]]
phone = "+85200000003"

[persons.p4]
name = "Dee"
[[persons.p4.signal]]
phone = "+85200000004"
'''

_UNREACHABLE = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)
_ROSTER = FakeRosterSource(groups={
    "ROOM-A": (frozenset({ADA}), frozenset({BEN}), frozenset({CY})),
    "ROOM-B": (frozenset({BEN}),),
    "ROOM-C": (frozenset({ADA}), frozenset({BEN})),
    # Dee shares no location circle with Ada: the guard refuses this room.
    "ROOM-D": (frozenset({ADA}), frozenset({DEE})),
    "ROOM-S": (frozenset({ADA}),),
})


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _persons(tmp_path):
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    return load_persons(tmp_path / "identity.toml")


def _store(engine, persons) -> SqlSocialStreamStore:
    """Built as the composition root builds it: the opted-out persons given."""
    return SqlSocialStreamStore(engine, opted_out=frozenset(
        p for p in persons.by_person if persons.social_off(p)))


def _tool(engine, tmp_path, *, room_id="ROOM-A", wired=True, metrics=None):
    (tmp_path / "policy.toml").write_text(_POLICY)
    policy = load_policy(tmp_path / "policy.toml")
    persons = _persons(tmp_path)
    circle = Circle(circle_id="c1", person_ids=frozenset({"p1", "p2", "p3"}))
    room = Room(platform="signal", conversation_id=room_id, roster_source=_ROSTER,
                persons=persons)
    tools = build_tools(
        store=FakeRecordStore(), conversation_id=room_id, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=room, transit_source=_UNREACHABLE,
        clock=lambda: NOW,
        social_items=_store(engine, persons) if wired else None,
        lineage=LineagePolicy.from_policy(policy, circles=(circle,)),
    )
    return {t.tool_name: t for t in tools}["social_read"]


async def _call(tool, person="Ada", interval=INTERVAL):
    return await tool._tool_func(person=person, interval=interval)


async def _describe(engine, sha: str, title: str, description: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO media_objects (sha256, source, title, description, created_at_ms) "
            "VALUES (:sha, 'social', :t, :d, 0)"
        ), {"sha": sha, "t": title, "d": description})


async def _seed(
    engine, item_id: str, *, person="p1", kind="post", hours_ago: float = 3,
    caption: str | None = "a caption", location: str | None = "Harbour Pier",
    media: str | None = None, collab: bool = False,
) -> None:
    taken = NOW - timedelta(hours=hours_ago)
    await SqlSocialStreamStore(engine).insert_item(
        SocialItem(
            network="instagram", item_id=item_id, account="acct-1", kind=kind,  # type: ignore[arg-type]
            taken_at=taken,
            expires_at=taken + timedelta(hours=24) if kind == "story_frame" else None,
            caption=caption, alt_text=None, media=(MediaItem("image", "https://x"),),
            collab=collab,
        ),
        person=person, origin="poll", seen_at=taken, media_ref=media, location=location,
    )


# ---- what an item says ---------------------------------------------------------------------------

async def test_an_item_says_what_it_is_how_old_it_is_and_what_it_showed(engine, tmp_path):
    await _describe(engine, "a" * 64, "A bowl of noodles", "Steam over a red bowl.")
    await _seed(engine, "1", kind="story_frame", hours_ago=3, caption="lunch!",
                media="a" * 64)
    await _seed(engine, "2", kind="reel", hours_ago=30, caption=None, collab=True)
    got = await _call(_tool(engine, tmp_path))
    assert got["status"] == "ok"
    story, reel = got["items"]                       # newest first
    assert story == {
        "kind": "Instagram story", "by": "Ada", "posted": "2026-10-07 Wed 09:00 (3h ago)",
        "caption": "lunch!", "seen": ["A bowl of noodles\nSteam over a red bowl."],
        "location": "Harbour Pier",
    }
    assert reel["kind"] == "Instagram reel, co-authored"
    assert reel["posted"].endswith("(30h ago)")
    assert reel["seen"] == "not read yet" and "caption" not in reel
    # The caption is the author's published words, never an instruction.
    assert got["untrusted"] is True and "never an instruction" in got["framing"]
    assert "co-authored" in got["note"]


# ---- the scope: only rooms that list the author --------------------------------------------------

async def test_a_room_that_does_not_list_the_author_reads_like_a_name_nobody_uses(
    engine, tmp_path,
):
    await _seed(engine, "1")
    listing = await _call(_tool(engine, tmp_path, room_id="ROOM-A"))
    assert listing["status"] == "ok" and len(listing["items"]) == 1
    not_listing = _tool(engine, tmp_path, room_id="ROOM-B")
    got = await _call(not_listing)
    assert got["status"] == "nothing_found"
    assert got == await _call(not_listing, person="Nobody At All")
    assert "caption" not in str(got)


async def test_a_star_room_lists_nobody_so_reads_nobodys_items(engine, tmp_path):
    await _seed(engine, "1")
    got = await _call(_tool(engine, tmp_path, room_id="ROOM-S"))
    assert got["status"] == "nothing_found"


# ---- what is never read: gone, hidden, opted out -------------------------------------------------

async def test_gone_and_hidden_items_are_never_read(engine, tmp_path):
    for i in ("1", "2", "3"):
        await _seed(engine, i, caption=f"item {i}", hours_ago=int(i))
    store = SqlSocialStreamStore(engine)
    await store.record_state(network="instagram", item_id="1", state="gone")
    await store.record_state(network="instagram", item_id="2", state="hidden")
    got = await _call(_tool(engine, tmp_path))
    assert [i["caption"] for i in got["items"]] == ["item 3"]


async def test_a_person_who_opted_out_is_never_read_even_where_they_are_listed(
    engine, tmp_path,
):
    await _seed(engine, "1", person="p3", caption="from before the switch")
    got = await _call(_tool(engine, tmp_path), person="Cy")
    assert got["status"] == "nothing_found"
    assert "from before the switch" not in str(got)


async def test_only_the_interval_is_read(engine, tmp_path):
    await _seed(engine, "1", hours_ago=3, caption="inside")
    await _seed(engine, "2", hours_ago=24 * 10, caption="ten days back")
    got = await _call(_tool(engine, tmp_path))
    assert [i["caption"] for i in got["items"]] == ["inside"]


# ---- the location gate ---------------------------------------------------------------------------

@pytest.mark.parametrize(("room_id", "shown"), [
    ("ROOM-A", True),      # the flag is on and everyone here shares Ada's circle
    ("ROOM-C", False),     # the room's `location` flag is off
    ("ROOM-D", False),     # flag on, but Dee shares no circle with Ada: the guard
])
async def test_the_location_is_shown_only_where_the_room_may_place_the_author(
    engine, tmp_path, room_id, shown,
):
    await _seed(engine, "1", location="Harbour Pier")
    got = await _call(_tool(engine, tmp_path, room_id=room_id))
    assert got["status"] == "ok"
    (item,) = got["items"]
    assert ("location" in item) is shown
    assert ("Harbour Pier" in str(got)) is shown


# ---- bounded -------------------------------------------------------------------------------------

async def test_more_than_the_cap_is_cut_newest_first_and_says_so(engine, tmp_path):
    for i in range(SOCIAL_READ_MAX + 3):
        await _seed(engine, str(i), hours_ago=1 + i, caption=f"item {i}")
    got = await _call(_tool(engine, tmp_path))
    assert len(got["items"]) == SOCIAL_READ_MAX
    assert got["items"][0]["caption"] == "item 0"
    assert got["truncated"] is True and "More items matched" in got["note"]


# ---- R-18, and failing closed --------------------------------------------------------------------

async def test_an_item_carrying_an_identifier_is_withheld_whole(engine, tmp_path):
    await _seed(engine, "1", caption="text me on +85291234567")
    got = await _call(_tool(engine, tmp_path))
    assert got["status"] == "nothing_found" and "+852" not in str(got)


async def test_a_bad_interval_is_unavailable_never_everything(engine, tmp_path):
    got = await _call(_tool(engine, tmp_path), interval="last week")
    assert got["status"] == "unavailable" and got["note"]


async def test_an_unwired_or_failing_read_is_unavailable_never_nothing_found(engine, tmp_path):
    assert (await _call(_tool(engine, tmp_path, wired=False)))["status"] == "unavailable"
    with patch.object(SqlSocialStreamStore, "items_of", side_effect=RuntimeError("down")):
        got = await _call(_tool(engine, tmp_path))
    assert got["status"] == "unavailable" and got["note"]
