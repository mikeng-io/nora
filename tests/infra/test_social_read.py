"""`social_read` and `SqlSocialStreamStore.items_of`, against Postgres.

The read (docs/design/cognition/loop.md, docs/design/cognition/loop.md 's
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

import pytest
import sqlalchemy as sa

from nora.domain.social_stream import MediaItem, SocialItem
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.social_stream import SqlSocialStreamStore, readable_social_store
from nora.infra.transit import TransitClient
from nora.persons import load_persons
from nora.testing import FakeRosterSource

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
INTERVAL = "2026-10-01/2026-10-07"
ADA, BEN, CY, DEE, EVE = (
    "+85200000001", "+85200000002", "+85200000003", "+85200000004", "+85200000005",
)

#: Each fixture room has its own unit to show that the read is scoped by
#: who a room lists, never by its unit.
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

[[conversations]]
platform = "signal"
id = "ROOM-N"
label = "Room Namesakes"
persons = ["p1", "p5"]
unit = "u6"
'''

_IDENTITY = '''
[persons.p1]
name = "Ada"
[[persons.p1.signal]]
phone = "+85200000001"

[persons.p2]
name = "Ben"
aliases = ["Benny"]
[[persons.p2.signal]]
phone = "+85200000002"

[persons.p3]
name = "Cy"
social = false
former_person_ids = ["cy_old"]
[[persons.p3.signal]]
phone = "+85200000003"

[persons.p4]
name = "Dee"
former_person_ids = ["dee_old"]
[[persons.p4.signal]]
phone = "+85200000004"

[persons.p5]
name = "Ada"
first_name = "Ada"
last_name = "Wong"
[[persons.p5.signal]]
phone = "+85200000005"
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
    "ROOM-N": (frozenset({ADA}), frozenset({EVE})),
})


@pytest.fixture
async def engine(clean_pg_url):
    eng = create_sql_engine(clean_pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _persons(tmp_path):
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    return load_persons(tmp_path / "identity.toml")


def _store(engine, persons) -> SqlSocialStreamStore:
    """Built by the composition root's own helper, so a test here reads what
    production reads (the opted-out persons under every id they had)."""
    return readable_social_store(engine, persons)




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



# ---- the scope: only rooms that list the author --------------------------------------------------





# ---- what is never read: gone, hidden, opted out -------------------------------------------------







# ---- the location gate ---------------------------------------------------------------------------



# ---- bounded -------------------------------------------------------------------------------------



# ---- R-18, and failing closed --------------------------------------------------------------------







# ---- who a name means ----------------------------------------------------------------------------









# ---- a renamed person ----------------------------------------------------------------------------





# ---- one item's own bound ------------------------------------------------------------------------



# ---- a tag the author changed or removed ---------------------------------------------------------

