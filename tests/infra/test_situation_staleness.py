"""Read situation staleness and Standing's carried fields from scoped Postgres rows."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.lineage import RoomFacts
from nora.infra.sql.cognitive_graph_schema import EDGE, INTENTION
from nora.memory.adapters.graph_row_render import render_graph_row
from nora.memory.domain.retrieval import ReadingRoom, RetrievedEdge
from tests.infra.test_retrieval_scope import (
    AT,
    BOUNDS,
    DM,
    OTHER,
    OTHER_ROOM,
    PERSON,
    READ,
    ROOM,
    UNIT,
    Scenario,
    references,
    write_edge,
)
from tests.infra.test_retrieval_scope import scenario as scope_scenario

scenario = scope_scenario


async def intention(scenario: Scenario, graph_id: str, **fields) -> None:
    await scenario.graph.create(INTENTION, {
        "id": graph_id, "unit": UNIT, "kind": "intention", "basis": "committed",
        "state": "open", "writer": "fixture", "exposure": "internal", "origin_room": ROOM,
        "asserted_at": AT, "owner": "nora", "owed_to": PERSON, "identities": [PERSON],
        "form": "achieve", "done_when_form": "act_event", "done_when": {"act": "fixture"},
        "delivery_mode": "when_it_comes_up", **fields,
    })


async def serves(scenario: Scenario, child: str, parent: str) -> None:
    await scenario.graph.create(EDGE, {
        "id": f"edge:serves:{child}:{parent}", "unit": UNIT, "kind": "edge",
        "basis": "committed", "state": "live", "exposure": "internal", "writer": "fixture",
        "type": "serves", "src_id": child, "dst_id": parent, "asserted_at": AT,
    })


@contextmanager
def refuse_count(engine: AsyncEngine, marker: str) -> Iterator[None]:
    def fail_count(conn, cursor, statement, parameters, context, executemany):
        if marker in statement:
            cursor.execute("SELECT 1/0")

    sa.event.listen(engine.sync_engine, "before_cursor_execute", fail_count)
    try:
        yield
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", fail_count)


async def test_situation_counts_three_visible_later_room_messages(scenario) -> None:
    await scenario.claim("situation", part="situation", origin_room=f"signal:{ROOM}")
    await scenario.item("same-instant")
    await scenario.item("excluded", at=AT + timedelta(minutes=1), excluded_identities=[PERSON])
    await scenario.item("after-pass", at=AT + timedelta(minutes=4))
    for index in range(1, 4):
        await scenario.item(f"later-{index}", at=AT + timedelta(minutes=index))
    now = AT + timedelta(minutes=3)
    result = await scenario.service().retrieve(READ, references("situation"), BOUNDS, now=now)
    (row,) = result.rows
    assert (result.status, row.items_since) == ("ok", 3)
    rendered = render_graph_row(
        row, now=now, dm_rooms=frozenset(),
        reading_room=RoomFacts("signal", frozenset({ROOM}), UNIT, frozenset({PERSON})),
    )
    assert "items_since=3" in rendered


async def test_other_room_messages_do_not_count(scenario) -> None:
    await scenario.claim("situation", part="situation")
    await scenario.item("in-room", at=AT + timedelta(minutes=1))
    await scenario.item("other-room", room=OTHER_ROOM, at=AT + timedelta(minutes=1))
    result = await scenario.service().retrieve(READ, references("situation"), BOUNDS)
    (row,) = result.rows
    assert (result.status, row.items_since) == ("ok", 1)


async def test_as_of_counts_only_items_up_to_its_instant(scenario) -> None:
    before = await scenario.now()
    await scenario.claim("situation", part="situation")
    await scenario.item("first", at=AT + timedelta(minutes=1))
    await scenario.item("event-after-cut", at=before + timedelta(days=1))
    at = await scenario.now()
    await scenario.item("registered-after-cut", at=AT + timedelta(minutes=2))
    now = before + timedelta(days=2)
    service = scenario.service()
    anchors = references("situation")
    current = await service.retrieve(READ, anchors, BOUNDS, now=now)
    earlier = await service.retrieve(READ, anchors, BOUNDS, now=now, as_of=at)
    assert (current.rows[0].items_since, earlier.rows[0].items_since) == (3, 1)


async def test_non_situation_claim_has_no_items_since(scenario) -> None:
    await scenario.claim("situation", part="situation")
    await scenario.claim("fact")
    await scenario.item("later", at=AT + timedelta(minutes=1))
    result = await scenario.service().retrieve(READ, references("situation", "fact"), BOUNDS)
    assert {row.graph_id: row.items_since for row in result.rows} == {"situation": 1, "fact": None}


async def test_dm_count_is_unknown_outside_its_dm(scenario) -> None:
    await scenario.claim("dm-situation", part="situation", origin_room=f"signal:{DM}")
    await scenario.item("dm-message", room=DM, dm=True, person=OTHER, at=AT + timedelta(minutes=1))
    service = scenario.service()
    anchors = references("dm-situation")
    outside = await service.retrieve(READ, anchors, BOUNDS)
    inside = await service.retrieve(ReadingRoom("signal", DM), anchors, BOUNDS)
    (row,) = outside.rows
    assert (outside.status, row.items_since, row.items_since_cause) == (
        "ok", None, "items_since_out_of_scope",
    )
    assert (inside.status, inside.rows[0].items_since) == ("ok", 1)


async def test_failed_count_returns_a_partial_row_and_keeps_the_snapshot_usable(scenario) -> None:
    await scenario.claim("situation", part="situation", identities=[PERSON])
    await scenario.item("later", at=AT + timedelta(minutes=1))
    service = scenario.service()
    anchors = references("situation")
    assert (await service.retrieve(READ, anchors, BOUNDS)).rows[0].items_since == 1
    with refuse_count(scenario.engine, "items_since_claim"):
        result = await service.retrieve(READ, anchors, BOUNDS)
    (row,) = result.rows
    assert (result.status, result.cause, row.items_since) == (
        "partial", "items_since_unavailable", None,
    )


async def test_retrieval_carries_bounds_endings_and_outgoing_targets(scenario) -> None:
    await intention(scenario, "parent")
    await scenario.item("witness")
    trigger = {"kind": "time", "at": AT.isoformat(), "zone": "UTC"}
    await intention(
        scenario, "child", state="ended", ending="achieved", ended_at=AT,
        trigger=trigger, delivery_mode="choose_moment",
    )
    await serves(scenario, "child", "parent")
    await write_edge(scenario, "child", "witness", "closed_by")
    bounds = replace(BOUNDS, depth=0)
    result = await scenario.service().retrieve(READ, references("child"), bounds)
    (row,) = result.rows
    assert result.bounds == bounds
    assert {key: row.close_when[key] for key in ("trigger", "ending", "ended_at")} == {
        "trigger": trigger, "ending": "achieved", "ended_at": AT.isoformat(),
    }
    assert set(row.edges) == {
        RetrievedEdge("serves", "child", "parent"), RetrievedEdge("closed_by", "child", "witness"),
    }


async def test_situation_carries_the_room_pointer_id_outside_the_result(scenario) -> None:
    await scenario.entity("room-pointer", pointer_kind="room", pointer_ref=ROOM)
    await scenario.claim("situation", part="situation")
    await write_edge(scenario, "situation", "room-pointer", "about")
    result = await scenario.service().retrieve(
        READ, references("situation"), replace(BOUNDS, depth=0),
    )
    (row,) = result.rows
    assert row.edges == (RetrievedEdge("about", "situation", "room-pointer"),)
