"""`rollup_read` over a REAL Rollup build.

The world is the step-2 gate's own: two members' fixes and polls, their unit's
messages, replies, reactions and Nora's sends, folded by the real builder
(`tests/integration/test_rollup_builds.py`). The store reads what that build
holds, and the tool reads it under lineage for the room it is bound to.

What each test would catch if its behaviour were reverted is named in
`scripts/mutations/cognition-reads.json`. Rooms, people and places are
placeholders.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.agent.whereis import Circle
from nora.domain.rollup_read import RollupReadQuery
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.rollups import SqlRollupStore
from nora.infra.sql.schema import MESSAGES, PRESENCE_STAYS
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender
from tests.integration.test_rollup_builds import _START, _WATERMARK, _runner, _seed, _t

DAY = 86_400_000
NOW = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)

# Room 1 lists both members and is location-trusted; room 2 (same unit) lists
# one of them and is not; room 3 is another unit.
_POLICY = '''
default = "deny"

[[conversations]]
platform = "signal"
id = "group-1"
label = "Big room"
persons = ["a", "b"]
unit = "friends"
location = true

[[conversations]]
platform = "discord"
id = "group-2"
label = "Small room"
persons = ["a"]
unit = "friends"

[[conversations]]
platform = "signal"
id = "group-3"
label = "Other unit"
persons = ["a"]
unit = "elsewhere"
location = true
'''

_IDENTITY = '''
[persons.a]
name = "Ada"
[[persons.a.signal]]
phone = "+85200000001"
[[persons.a.discord]]
user_id = "1001"

[persons.b]
name = "Ben"
[[persons.b.signal]]
phone = "+85200000002"
'''

_UNREACHABLE = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)
_BOTH = FakeRosterSource(groups={"group-1": (frozenset({"+85200000001"}),
                                             frozenset({"+85200000002"}))})
_ADA_ONLY = FakeRosterSource(groups={"group-2": (frozenset({"1001"}),)})
DAY0, DAY1 = "2026-09-21", "2026-09-22"


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def built(engine):
    """The seeded world, built once and switched current; returns the build id."""
    await _seed(engine, SqlMobilityStore(engine))
    return await _runner(engine).rebuild(through_ms=_WATERMARK)


def _world(tmp_path):
    (tmp_path / "policy.toml").write_text(_POLICY)
    (tmp_path / "identity.toml").write_text(_IDENTITY)
    return load_policy(tmp_path / "policy.toml"), load_persons(tmp_path / "identity.toml")


def _tool(engine, tmp_path, *, room_id="group-1", platform="signal", roster=_BOTH,
          rollup=True, lineage=True, metrics=None, name="rollup_read"):
    policy, persons = _world(tmp_path)
    circle = Circle(
        circle_id="c1", person_ids=frozenset({"a", "b"}),
        people=tuple(persons.by_person[p] for p in ("a", "b")),
    )
    room = Room(platform=platform, conversation_id=room_id, roster_source=roster,
                persons=persons)
    tools = build_tools(
        store=FakeRecordStore(), conversation_id=room_id, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=room, transit_source=_UNREACHABLE,
        clock=lambda: NOW,
        rollup=SqlRollupRead(engine) if rollup else None,
        lineage=LineagePolicy.from_policy(policy, circles=(circle,)) if lineage else None,
    )
    return {t.tool_name: t for t in tools}[name]


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


def _q(kind, subjects=("a",), *, start=_START, end=_WATERMARK, **kw):
    return RollupReadQuery(
        kind=kind, unit="friends", subjects=tuple(subjects), start_ms=start, end_ms=end, **kw,
    )


# ---- the store: the current build, the unit, no coordinate ---------------------------------------

async def test_every_kind_reads_typed_rows_from_the_current_build_and_no_row_has_a_coordinate(
    engine, built,
):
    store = SqlRollupRead(engine)
    seen = {}
    for kind, subjects in (("stay", ("a",)), ("trip", ("a",)), ("colocation", ("a", "b")),
                           ("outing", ("a", "b")), ("daybook", ("a",)), ("tie", ("a", "b"))):
        got = await store.read(_q(kind, subjects, match="all"))
        assert got.build_id == built and got.through_ms == _WATERMARK, kind
        assert got.rows, f"{kind}: the fixture must produce it"
        seen[kind] = got.rows
        for row in got.rows:
            assert row.kind == kind
            assert row.valid_from_ms <= row.valid_to_ms
            assert row.lineage.section("row"), "unknown lineage would pass nowhere"
            assert not {"lat", "lng"} & set(row.fields), (kind, row.fields)
    # the citation key, exactly as `Citation` documents it
    assert {r.subject for r in seen["stay"]} == {"a"}
    assert {r.subject for r in seen["outing"]} == {"friends|a,b"}
    assert {r.subject for r in seen["daybook"]} == {"friends|a"}
    assert {r.subject for r in seen["tie"]} == {"friends|a,b"}


async def test_a_row_resolves_by_its_own_key_after_it_is_read(engine, built):
    """The row carries `(kind, subject, interval)` and that key resolves
    against the current build, so a note can cite what it was shown."""
    from nora.domain.rollup import Citation

    store = SqlRollupRead(engine)
    for kind, subjects in (("outing", ("a", "b")), ("stay", ("a",)), ("daybook", ("a",))):
        (first, *_rest) = (await store.read(_q(kind, subjects, match="all"))).rows
        resolved = await SqlRollupStore(engine).resolve(
            Citation(first.kind, first.subject, first.valid_from_ms, first.valid_to_ms),
        )
        assert not resolved.unsupported, kind


@pytest.mark.parametrize("kind, table, column", [
    ("daybook", "rollup_daybook", "person_id"), ("stay", "presence_stays", "subject_id"),
])
async def test_a_superseded_build_is_never_read(engine, built, kind, table, column):
    """A rebuild switches the pointer and keeps the old rows; the read is the
    CURRENT build's alone, for a unit rollup and a person-scoped one."""
    store = SqlRollupRead(engine)
    first = (await store.read(_q(kind))).rows
    second_build = await _runner(engine).rebuild(through_ms=_WATERMARK)
    assert second_build != built
    async with engine.connect() as conn:
        stored = (await conn.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE {column} = 'a'"  # noqa: S608 (test constants)
        ))).scalar_one()
    again = await store.read(_q(kind))
    assert again.build_id == second_build
    assert len(again.rows) == len(first) and stored == 2 * len(first)   # two builds stored


async def test_a_message_hidden_after_a_build_stops_counting_at_the_next_one(engine, built):
    """Withdrawal: the unit rollups' inputs exclude hidden
    messages, so a hidden message is absent from the row once it is
    recomputed, and `through` says how fresh the rows read are."""
    store = SqlRollupRead(engine)
    before = (await store.read(_q("daybook"))).rows
    assert sum(r.fields["messages"] for r in before) > 0
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.person_id == "a")
                           .values(hidden_at=1))
    await _runner(engine).rebuild(through_ms=_WATERMARK)
    after = await store.read(_q("daybook"))
    assert sum(r.fields["messages"] for r in after.rows) == 0
    ties = (await store.read(_q("tie", ("a", "b"), match="all"))).rows
    assert sum(r.fields["replies"][1] for r in ties) == 0          # b's replies quoted a's


async def test_the_unit_is_the_read_boundary(engine, built):
    store = SqlRollupRead(engine)
    other = await store.read(RollupReadQuery(
        kind="daybook", unit="elsewhere", subjects=("a",), start_ms=_START, end_ms=_WATERMARK,
    ))
    assert other.build_id == built and other.rows == ()


async def test_the_interval_overlaps_and_the_page_says_when_there_is_more(engine, built):
    store = SqlRollupRead(engine)
    day0 = await store.read(_q("daybook", end=_t(1, 0)))
    assert [r.fields["local_date"] for r in day0.rows] == [DAY0]
    before = await store.read(_q("daybook", start=_START - 30 * DAY, end=_START - DAY))
    assert before.rows == () and before.build_id == built     # an empty interval, a real build
    paged = await store.read(_q("daybook", limit=1))
    assert len(paged.rows) == 1 and paged.more is True
    assert (await store.read(_q("daybook", limit=2))).more is False


async def test_a_history_past_ninety_days_is_readable_where_where_been_stops(engine, built):
    """`rollup_read` is not capped at `where_been`'s 90 days. A stay 200 days
    before now is returned; the presence store's own window read clamps at 90
    and does not hold it."""
    old_start = int(NOW.timestamp() * 1000) - 200 * DAY
    async with engine.begin() as conn:
        build = (await conn.execute(sa.text(
            "SELECT id FROM rollup_build WHERE status = 'current'"))).scalar_one()
        await conn.execute(sa.insert(PRESENCE_STAYS).values(
            subject_id="a", person_id="a", circle_id="c1", started_ts=old_start,
            ended_ts=old_start + 3_600_000, lat=22.3, lng=114.1, radius_m=100.0,
            fix_count=3, build_id=build, lineage=[["presence", "a", None]],
        ))
    rows = (await SqlRollupRead(engine).read(
        _q("stay", start=old_start - DAY, end=old_start + DAY))).rows
    assert [r.valid_from_ms for r in rows] == [old_start]
    windowed = await SqlMobilityStore(engine).stays_for_subject_window(
        subject_id="a", window_ms=300 * DAY,
    )
    assert old_start not in {s["started_ts"] for s in windowed}      # the 90-day clamp


async def test_no_current_build_is_no_build_not_an_empty_interval(engine):
    got = await SqlRollupRead(engine).read(_q("daybook"))
    assert got.build_id is None and got.rows == ()


async def test_reading_starts_no_build(engine, built):
    """A Rollup read must not trigger a rebuild (`READ_TOOL_AUDIT`)."""
    async with engine.connect() as conn:
        def builds():
            return conn.execute(sa.text("SELECT id, status FROM rollup_build ORDER BY id"))

        before = [tuple(r) for r in await builds()]
    store = SqlRollupRead(engine)
    for kind in ("stay", "daybook", "tie"):
        await store.read(_q(kind, ("a", "b"), match="any"))
    async with engine.connect() as conn:
        after = [tuple(r) for r in await conn.execute(sa.text(
            "SELECT id, status FROM rollup_build ORDER BY id"))]
    assert after == before == [(built, "current")]


# ---- the tool: lineage for the room, the person in this room, the statuses -----------------------

async def test_the_tool_returns_typed_rows_keyed_for_citation(engine, built, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="daybook", subject="Ada",
                         interval=f"{DAY0}/{DAY1}")
    assert result["status"] == "ok"
    assert result["through"].startswith("2026-09-22T23:30")
    rows = result["rows"]
    assert [r["day"] for r in rows] == [DAY0, DAY1]
    first = rows[0]
    assert (first["kind"], first["subject"], first["who"]) == ("daybook", "friends|a", "Ada")
    assert first["from"].startswith("2026-09-21T00:00") and first["to"].startswith("2026-09-22")
    assert first["rooms"] == {"Big room": first["messages"]}      # a label, never a room id
    text = json.dumps(result)
    assert "group-1" not in text and "lat" not in text          # no raw room id, no coordinate


async def test_two_people_are_a_pair_and_a_pair_is_every_one_of_them(engine, built, tmp_path):
    tool = _tool(engine, tmp_path)
    tie = await _call(tool, kind="tie", subject="Ada, Ben", interval=f"{DAY0}/{DAY1}")
    assert tie["status"] == "ok"
    row = tie["rows"][0]
    assert row["who"] == ["Ada", "Ben"] and row["subject"] == "friends|a,b"
    assert set(row["replies"]) == {"a_to_b", "b_to_a"}
    outing = await _call(tool, kind="outing", subject="Ada, Ben", interval=f"{DAY0}/{DAY1}")
    assert outing["status"] == "ok" and all(r["who"] == ["Ada", "Ben"] for r in outing["rows"])
    # a place is words: the pub has no name, and the note says not to guess where
    assert {r["place"] for r in outing["rows"]} == {"an unlabelled place near Pub Street"}
    assert "not a guess about where" in outing["note"]


async def test_a_stay_names_its_place_in_words_and_says_when_it_is_unlabelled(
    engine, built, tmp_path,
):
    result = await _call(_tool(engine, tmp_path), kind="stay", subject="Ada",
                         interval=f"{DAY0}/{DAY1}")
    assert result["status"] == "ok"
    for row in result["rows"]:
        assert row["who"] == "Ada" and row["mins"] > 0
        assert set(row) >= {"kind", "subject", "from", "to", "place"}
    places = {r["place"] for r in result["rows"]}
    assert places == {"home near a Home Road", "work near Office Tower",
                      "an unlabelled place near Pub Street"}


async def test_a_room_that_may_not_be_shown_a_row_is_told_rows_were_left_out_and_nothing_else(
    engine, built, tmp_path,
):
    """Room 2 is not location-trusted: every stay and co-location rests on
    `presence`, which needs the room's flag AND the guard. It gets no row, no
    place and no time, and is told rows exist that it may not see, in a note
    that does not describe them."""
    tool = _tool(engine, tmp_path, room_id="group-2", platform="discord", roster=_ADA_ONLY)
    for kind in ("stay", "trip"):
        result = await _call(tool, kind=kind, subject="Ada", interval=f"{DAY0}/{DAY1}")
        assert result["status"] == "nothing_found", kind
        assert "not shown in this conversation" in result["note"]
        assert "rows" not in result
    # its own unit's daybook rests on messages of room 1 AND presence: withheld too
    book = await _call(tool, kind="daybook", subject="Ada", interval=f"{DAY0}/{DAY1}")
    assert book["status"] == "nothing_found" and "rows" not in book


async def test_a_name_resolves_only_among_this_rooms_people(engine, built, tmp_path):
    tool = _tool(engine, tmp_path, room_id="group-2", platform="discord", roster=_ADA_ONLY)
    ben = await _call(tool, kind="daybook", subject="Ben", interval=DAY0)
    nobody = await _call(tool, kind="daybook", subject="Nobody", interval=DAY0)
    assert ben["status"] == nobody["status"] == "nothing_found"
    assert ben["note"] == nobody["note"]                 # «not here» and «nobody» are one answer


async def test_the_other_units_room_reads_nothing_of_this_units(engine, built, tmp_path):
    tool = _tool(engine, tmp_path, room_id="group-3", platform="signal",
                 roster=FakeRosterSource(groups={"group-3": (frozenset({"+85200000001"}),)}))
    result = await _call(tool, kind="daybook", subject="Ada", interval=f"{DAY0}/{DAY1}")
    assert result["status"] == "nothing_found" and "rows" not in result


async def test_nothing_in_the_interval_says_how_far_the_build_reads(engine, built, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="daybook", subject="Ada",
                         interval="2026-01-01")
    assert result["status"] == "nothing_found" and "through" in result
    assert "not that nothing happened" in result["note"]


async def test_everyone_reads_the_people_in_this_room(engine, built, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="daybook", subject="everyone",
                         interval=DAY0)
    assert {r["who"] for r in result["rows"]} == {"Ada", "Ben"}


@pytest.mark.parametrize("kwargs, why", [
    ({"rollup": False}, "no rollup read wired"),
    ({"lineage": False}, "no lineage policy wired"),
    ({"room_id": "NOT-A-ROOM"}, "a room outside the policy"),
    ({"roster": FakeRosterSource(fail=True)}, "the roster could not be read"),
])
async def test_what_it_cannot_check_is_unavailable(engine, built, tmp_path, kwargs, why):
    result = await _call(_tool(engine, tmp_path, **kwargs), kind="daybook", subject="Ada",
                         interval=DAY0)
    assert result["status"] == "unavailable", why
    assert "rows" not in result
    assert "No rollup build" not in result["note"], "it must fail for ITS reason, a build exists"


@pytest.mark.parametrize("interval", [
    "last week", "2026-09-22/2026-09-21", "2026-09-21/2026-09-22/2026-09-23", "", "9999-99-99",
])
async def test_an_unreadable_interval_is_unavailable_never_everything(
    engine, built, tmp_path, interval,
):
    result = await _call(_tool(engine, tmp_path), kind="daybook", subject="Ada",
                         interval=interval)
    assert result["status"] == "unavailable" and "ISO 8601" in result["note"]


async def test_a_subject_the_kind_cannot_take_is_unavailable(engine, built, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="daybook", subject="Ada, Ben",
                         interval=DAY0)
    assert result["status"] == "unavailable"
    assert (await _call(_tool(engine, tmp_path), kind="tie", subject=" , ",
                        interval=DAY0))["status"] == "unavailable"


async def test_no_current_build_is_unavailable_not_nothing_found(engine, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="daybook", subject="Ada",
                         interval=DAY0)
    assert result["status"] == "unavailable" and "No rollup build is current" in result["note"]


async def test_every_call_is_counted_by_its_status(engine, built, tmp_path):
    metrics = Metrics()
    tool = _tool(engine, tmp_path, metrics=metrics)
    await _call(tool, kind="daybook", subject="Ada", interval=DAY0)
    await _call(tool, kind="daybook", subject="Ada", interval="2026-01-01")
    await _call(tool, kind="daybook", subject="Ada", interval="nope")
    for status in ("ok", "nothing_found", "unavailable"):
        assert metrics.get("nora_tool_calls_total", tool="rollup_read", status=status) == 1


# ---- an empty subject is never «everyone» --------------------------------------------------------

def _permissive(monkeypatch):
    """Lineage that shows every row, so it cannot mask a filter that went missing:
    what these tests measure is the subject filter and nothing after it."""
    monkeypatch.setattr("nora.agent.tools.visible_rows", lambda rows, target: (list(rows), 0))


_NOBODY_HERE = FakeRosterSource(groups={"group-1": ()})
_ONLY_AN_UNMAPPED_ACCOUNT = FakeRosterSource(groups={"group-1": (frozenset({"+85299999999"}),)})


@pytest.mark.parametrize("roster, why", [
    (_NOBODY_HERE, "a roster with nobody in it"),
    (_ONLY_AN_UNMAPPED_ACCOUNT, "one account no identity.toml block names"),
])
@pytest.mark.parametrize("kind", ["stay", "trip", "daybook", "colocation", "outing", "tie"])
async def test_everyone_in_a_room_with_no_resolved_people_is_nothing_found_not_every_row(
    engine, built, tmp_path, monkeypatch, kind, roster, why,
):
    _permissive(monkeypatch)
    tool = _tool(engine, tmp_path, roster=roster)
    result = await _call(tool, kind=kind, subject="everyone", interval=f"{DAY0}/{DAY1}")
    assert result["status"] == "nothing_found", why
    assert "rows" not in result, why


async def test_a_named_person_alone_narrows_the_read_with_lineage_out_of_the_way(
    engine, built, tmp_path, monkeypatch,
):
    """The subject filter, measured without lineage: Ada's daybook and only Ada's."""
    _permissive(monkeypatch)
    everyone = await _call(_tool(engine, tmp_path), kind="daybook", subject="everyone",
                           interval=f"{DAY0}/{DAY1}")
    ada = await _call(_tool(engine, tmp_path), kind="daybook", subject="Ada",
                      interval=f"{DAY0}/{DAY1}")
    assert {r["who"] for r in everyone["rows"]} == {"Ada", "Ben"}
    assert ada["status"] == "ok" and {r["who"] for r in ada["rows"]} == {"Ada"}


@pytest.mark.parametrize("kind", ["stay", "trip", "daybook", "colocation", "outing", "tie"])
async def test_the_store_refuses_an_empty_subject_set_it_never_reads_it_as_all(
    engine, built, kind,
):
    with pytest.raises(ValueError, match="at least one subject"):
        await SqlRollupRead(engine).read(_q(kind, subjects=()))


async def test_everyone_beside_a_name_is_refused_not_guessed_at(engine, built, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="colocation",
                         subject="everyone, Ada", interval=DAY0)
    assert result["status"] == "unavailable" and "everyone" in result["note"]
    assert "rows" not in result


async def test_a_kind_that_is_not_one_says_which_kinds_there_are(engine, built, tmp_path):
    result = await _call(_tool(engine, tmp_path), kind="place", subject="Ada", interval=DAY0)
    assert result["status"] == "unavailable"
    assert "not a rollup kind" in result["note"] and "daybook" in result["note"]
