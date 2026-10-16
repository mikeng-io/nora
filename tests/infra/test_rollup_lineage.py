"""A Rollup row's lineage, as the writer stores it, read by the lineage
predicates (DEC-236).

There is one lineage format in the store: an array of
`[kind, subject, origin]` triples, rooms keyed `platform:conversation_id`.
A Rollup row carries ONE set for the whole row, so the predicates read it as
the row's `ROW` section, never as malformed. This writes a real tie through
`SqlRollupStore.apply`, reads it back through `rows`, and asks the predicates
about it; and it pins the stored shape the writer produces, so a change on
either side reddens here.

Rooms, units and people are placeholders.
"""

from __future__ import annotations

import pytest

from nora.agent.lineage import section_permits
from nora.domain.lineage import ROW, Lineage, LiveRoom, RenderRoom, RoomFacts, message
from nora.domain.rollup import Tie
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.rollups import SqlRollupStore
from nora.infra.sql.schema import ROLLUP_TIE

A = RoomFacts(platform="signal", ids=frozenset({"ROOM-A"}), unit="u1",
              persons=frozenset({"p1", "p2", "p3"}))
B = RoomFacts(platform="discord", ids=frozenset({"ROOM-B"}), unit="u1",
              persons=frozenset({"p1", "p2"}))
D = RoomFacts(platform="whatsapp", ids=frozenset({"ROOM-D"}), unit="u2",
              persons=frozenset({"p1", "p2"}))
ROOMS = (A, B, D)
MEMBERS = LiveRoom(person_ids=frozenset({"p1", "p2"}))


def _in(room: RoomFacts) -> RenderRoom:
    return RenderRoom(room=room, rooms=ROOMS, live=MEMBERS, mode="on")


@pytest.fixture
async def engine(pg_url):
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _tie_row(engine) -> dict:
    """A tie between two members of unit u1, from what they said in room A
    only (no co-location, so no presence entry), written and read by the
    store."""
    store = SqlRollupStore(engine)
    build = await store.create_build(code_version="test", params_sha256="0" * 64)
    await store.apply(
        build_id=build, unit="u1", colocations=(), outings=(), daybooks=(),
        ties=(Tie(
            unit="u1", person_a="p1", person_b="p2", iso_week="2026-W40",
            replies_ab=2, replies_ba=1, mentions_ab=0, mentions_ba=0,
            reactions_ab=1, reactions_ba=0, colocation_ms=0,
            rooms=("signal:ROOM-A",), valid_from_ms=0, valid_to_ms=1, as_of_ms=1,
        ),),
        responses=(),
    )
    [row] = await store.rows(table=ROLLUP_TIE, build_id=build)
    return row


async def test_step_2as_writer_stores_one_array_of_triples_for_the_row(engine):
    """The shape both sides agree on, pinned at the writer."""
    row = await _tie_row(engine)
    assert row["lineage"] == [["message", None, "signal:ROOM-A"]]
    lineage = Lineage.from_json(row["lineage"])
    assert lineage.section(ROW) == (message("signal:ROOM-A"),)


async def test_a_rollup_row_passes_in_a_room_of_its_unit_and_is_refused_outside_it(engine):
    """Read as the row's one set, not as malformed: shown in room A, where it
    was said, and in room B, whose people are all A's; refused in room D, of
    another unit, though D lists the same two people."""
    lineage = Lineage.from_json((await _tie_row(engine))["lineage"])
    entries = lineage.section(ROW)
    assert section_permits(entries, _in(A), origin=None)
    assert section_permits(entries, _in(B), origin=None)
    assert not section_permits(entries, _in(D), origin=None)
