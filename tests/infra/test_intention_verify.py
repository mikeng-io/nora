"""Verify uses the clock, in-unit health and the current intention head."""

# ruff: noqa: F811 - pytest resolves imported fixture names.

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.app.graph_observe import graph_observe
from nora.config import AnalyticsConfig
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.rollups import SqlRollupStore
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.verify import SqlIntentionVerification
from nora.memory.app.verify import IntentionVerifier
from nora.memory.wire import intention_verifier
from nora.testing.room_slots import FakeRoomSlots
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_condition_matcher import _edges, _health, _intention, _platform_act
from tests.infra.test_item_registration import (
    _AT,
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    _Writer,
    engine,  # noqa: F401
)

_DEADLINE = _AT + timedelta(minutes=10)
_NOW = _DEADLINE + timedelta(minutes=10)
_BEFORE = _AT - timedelta(minutes=1)
_INSIDE = _AT + timedelta(minutes=1)
_PERSON_EVENT = {
    "person_id": _PERSON, "platform": "signal", "room": _ROOM, "text": "Generated reply",
}


async def _tick(engine):
    await intention_verifier(
        engine, lambda: _NOW, max_poll_gap_seconds=900, room_slots=FakeRoomSlots({}),
    ).run_once()


@pytest.mark.parametrize(("form", "mode", "deadline", "drop_at", "ending", "ended_at"), [
    ("person_event", "achieve", None, _AT, "dropped", _AT),
    ("untyped", "achieve", _DEADLINE, None, "unknown", _DEADLINE),
    ("untyped", "maintain", None, _AT, "dropped", _AT),
    ("person_event", "maintain", None, _AT, "dropped", _AT),
    ("person_event", "achieve", _DEADLINE, _AT, "dropped", _AT),
    ("person_event", "achieve", _DEADLINE, _DEADLINE, "dropped", _DEADLINE),
    ("person_event", "achieve", _DEADLINE, _DEADLINE + timedelta(seconds=1), "leaked", _DEADLINE),
])
async def test_the_first_clock_ending_has_the_deadline_and_no_witness(
    engine, form, mode, deadline, drop_at, ending, ended_at,
):
    await _intention(
        engine, "due", form, _PERSON_EVENT if form == "person_event" else "Generated condition",
        mode=mode, deadline=deadline, drop=("deadline", {"at": drop_at.isoformat()})
        if drop_at is not None else None,
    )
    await _health(engine, "signal", "up", at=_BEFORE)
    await _health(engine, "signal", "up", at=_INSIDE)

    await _tick(engine)

    row = (await _rows(engine, INTENTION))["due"]
    assert (row["ending"], row["ended_at"]) == (ending, ended_at)
    assert await _edges(engine) == {}


@pytest.mark.parametrize(("outage", "has_probe", "ending"), [
    (None, True, "leaked"),
    (_INSIDE + timedelta(seconds=1), True, "unknown"),
    (_DEADLINE + timedelta(seconds=1), True, "leaked"),
    (None, False, "unknown"),
])
async def test_every_source_must_be_healthy_over_the_whole_lineage_window(
    engine, outage, has_probe, ending,
):
    await _intention(engine, "first", "person_event", _PERSON_EVENT, deadline=_DEADLINE)
    first = (await _rows(engine, INTENTION))["first"]
    graph = SqlCognitiveGraph(engine)
    await graph.revise(INTENTION, dict(
        first, id="revised", replaces="first", asserted_at=_AT + timedelta(minutes=5),
    ))
    await _health(engine, "signal", "up", at=_BEFORE)
    if has_probe:
        await _health(engine, "signal", "up", at=_INSIDE)
    if outage is not None:
        await _health(engine, "signal", "down", at=outage)
        await _health(engine, "signal", "up", at=outage + timedelta(seconds=1))

    await _tick(engine)

    rows = await _rows(engine, INTENTION)
    assert rows["first"]["state"] == "superseded"
    assert (rows["revised"]["ending"], rows["revised"]["ended_at"]) == (ending, _DEADLINE)


async def test_act_event_health_uses_the_attempts_destination(engine):
    act = _platform_act(destination="discord:generated-destination")
    act = replace(act, authority=replace(act.authority, unit=_UNIT, intention="due"))
    await _intention(
        engine, "due", "act_event", {"act_id": act.identity.id},
        deadline=_DEADLINE, origin_room=f"signal:{_ROOM}",
    )
    assert await SqlActStore(engine, _registration()).reserve(act)
    await _health(engine, "discord", "up", at=_BEFORE)
    await _health(engine, "discord", "up", at=_INSIDE)
    await _health(engine, "signal", "down", at=_INSIDE)

    await _tick(engine)

    assert (await _rows(engine, INTENTION))["due"]["ending"] == "leaked"


@pytest.mark.parametrize(("condition", "origin", "ending"), [
    ({"own_delivery": True}, f"signal:{_ROOM}", "leaked"),
    ({"own_delivery": True}, None, "unknown"),
    ({"act_id": "generated-missing"}, f"signal:{_ROOM}", "leaked"),
])
async def test_an_unattempted_delivery_depends_on_the_origin_platform(
    engine, condition, origin, ending,
):
    await _intention(
        engine, "due", "act_event", condition, deadline=_DEADLINE, origin_room=origin,
    )
    await _health(engine, "signal", "up", at=_BEFORE)
    await _health(engine, "signal", "up", at=_INSIDE)

    await _tick(engine)

    assert (await _rows(engine, INTENTION))["due"]["ending"] == ending


async def test_an_unresolvable_predicate_is_unknown(engine):
    await _intention(
        engine, "due", "predicate", {"graph_id": "generated-missing",
                                    "path": ["value"], "equals": True},
        deadline=_DEADLINE,
    )
    await _health(engine, "signal", "up", at=_BEFORE)
    await _health(engine, "signal", "up", at=_INSIDE)

    await _tick(engine)

    assert (await _rows(engine, INTENTION))["due"]["ending"] == "unknown"


class _Errors:
    def __init__(self):
        self.stages = []

    def inc(self, name, *, stage):
        assert name == "nora_errors_total"
        self.stages.append(stage)


async def test_a_witness_recorded_before_the_deadline_wins_on_the_observe_tick(engine):
    now = datetime.now(UTC)
    deadline = now + timedelta(minutes=1)
    await _intention(
        engine, "witnessed", "person_event", _PERSON_EVENT,
        asserted_at=now - timedelta(minutes=1), deadline=deadline,
    )
    await _intention(
        engine, "unwitnessed", "untyped", "Generated condition", deadline=deadline,
    )
    writer = _Writer(engine, _registration())
    await SqlRecordStore(engine, registration=writer.registration).append(
        message=replace(writer._message(), body=_PERSON_EVENT["text"]), person_id=_PERSON,
    )
    await _health(engine, "signal", "up", at=now - timedelta(seconds=1))
    errors = _Errors()
    observe = graph_observe(
        engine, lambda: deadline + timedelta(minutes=1), errors, analytics=AnalyticsConfig(),
        room_slots=FakeRoomSlots({}),
    )

    await observe.tick()

    rows = await _rows(engine, INTENTION)
    assert rows["witnessed"]["ending"] == "achieved"
    assert rows["unwitnessed"]["ending"] == "unknown"
    edges = await _edges(engine)
    assert set(edges) == {"witnessed"}
    assert edges["witnessed"]["occurred_at"] < deadline
    assert not errors.stages


class _ReviseDuringHealth(SqlRollupStore):
    def __init__(self, engine, graph, first):
        super().__init__(engine)
        self.graph = graph
        self.first = first

    async def health_rows(self, **kwargs):
        await self.graph.revise(INTENTION, dict(
            self.first, id="revised", replaces=self.first["id"], asserted_at=_INSIDE,
        ))
        return await super().health_rows(**kwargs)


async def test_a_head_revised_during_health_read_is_left_for_the_next_tick(engine):
    await _intention(engine, "first", "person_event", _PERSON_EVENT, deadline=_DEADLINE)
    first = (await _rows(engine, INTENTION))["first"]
    graph = SqlCognitiveGraph(engine)
    await _health(engine, "signal", "up", at=_BEFORE)
    await _health(engine, "signal", "up", at=_INSIDE)
    verifier = IntentionVerifier(
        SqlIntentionVerification(engine, graph), _ReviseDuringHealth(engine, graph, first),
        lambda: _NOW, max_poll_gap_ms=900_000, room_slots=FakeRoomSlots({}),
    )

    await verifier.run_once()

    rows = await _rows(engine, INTENTION)
    assert (rows["first"]["state"], rows["first"]["ending"]) == ("superseded", None)
    assert rows["revised"]["state"] == "open"

    await _tick(engine)
    assert (await _rows(engine, INTENTION))["revised"]["ending"] == "leaked"


async def test_the_clock_retires_watches_when_it_can_lock_the_maintain_head(engine):
    graph = SqlCognitiveGraph(engine)
    await _intention(
        engine, "due", "untyped", "Generated condition", mode="maintain",
        drop=("deadline", _AT.isoformat()),
    )
    await graph.create(ENTITY, {
        "id": "target", "unit": _UNIT, "kind": "entity", "basis": "inferred",
        "state": "admitted", "writer": "fixture", "asserted_at": _AT, "exposure": "internal",
        "name": "Generated source", "reasoning": "induced", "confidence": "high",
    })
    await graph.create(EDGE, {
        "id": "watch", "unit": _UNIT, "kind": "edge", "type": "watches", "basis": "committed",
        "state": "live", "writer": "fixture", "asserted_at": _AT,
        "src_id": "due", "dst_id": "target", "exposure": "internal",
    })

    async with engine.begin() as conn:
        await conn.execute(sa.select(INTENTION).where(INTENTION.c.id == "due").with_for_update())
        await _tick(engine)
        assert (await _rows(engine, INTENTION))["due"]["state"] == "open"
        assert (await _edges(engine, "watches"))["due"]["state"] == "live"

    await _tick(engine)
    assert (await _rows(engine, INTENTION))["due"]["ending"] == "dropped"
    assert (await _edges(engine, "watches"))["due"]["state"] == "retired"
