"""Observe publishes due occurrences for the open intention's trigger witnesses."""

# ruff: noqa: F811 - pytest resolves imported fixture names.

from datetime import timedelta

import sqlalchemy as sa

from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.schema import ROW_HISTORY
from nora.memory import wire
from tests.infra.test_condition_matcher import _head, _health, _intention
from tests.infra.test_item_registration import _UNIT, engine  # noqa: F401
from tests.infra.test_retrieval_scope import AT, PERSON, ROOM, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_situation_staleness import intention

_TEXT = "The generated event has started."
_PERSON_TRIGGER = {"kind": "condition", "form": "identity_event", "value": {
    "identity_id": PERSON, "platform": "signal", "room": ROOM, "text": _TEXT,
}}


async def _due(engine, unit):
    return [stimulus for stimulus in await SqlGraphStimuli(engine).read(unit)
            if stimulus.kind == "intention_due"]


async def _predicate_trigger(engine, head):
    await _intention(engine, "waiting", "act_event", {"act_id": "unrelated"}, trigger={
        "kind": "condition", "form": "predicate",
        "value": {"graph_id": head["graph_id"], "path": ["source_state", "state"],
                  "equals": "closed"},
    })


async def test_a_predicate_that_becomes_true_publishes_its_due_occurrence(engine):
    head = await _head(engine, "create", "open")
    await _health(engine, "signal", "up")
    await _predicate_trigger(engine, head)
    matcher = wire.condition_matcher(engine)
    await matcher.run_once()
    assert await _due(engine, _UNIT) == []

    current = await _head(engine, "revise", "closed")
    await matcher.run_once()
    [due] = await _due(engine, _UNIT)
    assert (due.graph_id, due.occurrence) == ("waiting", current["graph_id"])
    history = ROW_HISTORY.c
    async with engine.connect() as conn:
        recorded = await conn.scalar(sa.select(history.recorded_at).where(
            history.table_name == "platform_object_state",
            history.image["graph_id"].astext == current["graph_id"],
            history.superseded_at.is_(None),
        ))
    assert due.eligible_at == recorded


async def _schedule(scenario, graph_id="waiting", **fields):
    await intention(scenario, graph_id, delivery_mode="choose_moment", trigger=_PERSON_TRIGGER,
                    **fields)


async def test_a_second_tick_with_the_same_witness_publishes_nothing(scenario):
    await _schedule(scenario)
    await _health(scenario.engine, "signal", "up", unit=UNIT)
    stamp = AT + timedelta(seconds=10)
    await scenario.item("first", _TEXT, at=stamp)
    matcher = wire.condition_matcher(scenario.engine)
    await matcher.run_once()
    [due] = await _due(scenario.engine, UNIT)
    assert (due.graph_id, due.eligible_at) == ("waiting", stamp)
    await matcher.run_once()
    assert await _due(scenario.engine, UNIT) == [due]


async def test_a_later_distinct_witness_publishes_a_second_occurrence(scenario):
    await _schedule(scenario)
    await _health(scenario.engine, "signal", "up", unit=UNIT)
    matcher = wire.condition_matcher(scenario.engine)
    await scenario.item("first", _TEXT, at=AT)
    await matcher.run_once()
    [first] = await _due(scenario.engine, UNIT)
    stamp = AT + timedelta(seconds=10)
    await scenario.item("second", _TEXT, at=stamp)
    await matcher.run_once()
    assert [(due.occurrence, due.eligible_at) for due in await _due(scenario.engine, UNIT)] == [
        (first.occurrence, AT), ("second", stamp),
    ]


async def test_an_unhealthy_trigger_source_publishes_nothing(engine):
    head = await _head(engine, "create", "closed")
    await _predicate_trigger(engine, head)
    matcher = wire.condition_matcher(engine)
    await _health(engine, "signal", "down")
    await matcher.run_once()
    assert await _due(engine, _UNIT) == []
    await _health(engine, "signal", "up")
    await matcher.run_once()
    [due] = await _due(engine, _UNIT)
    assert due.occurrence == head["graph_id"]


async def test_an_ended_intention_publishes_no_due_occurrence(scenario):
    await _schedule(scenario, "ended", state="ended", ending="achieved", ended_at=AT)
    await _schedule(scenario)
    await _health(scenario.engine, "signal", "up", unit=UNIT)
    await scenario.item("witness", _TEXT, at=AT)
    await wire.condition_matcher(scenario.engine).run_once()
    [due] = await _due(scenario.engine, UNIT)
    assert (due.graph_id, due.occurrence) == ("waiting", "witness")


async def test_an_untyped_intention_with_a_deadline_can_fire(scenario):
    await _schedule(scenario, done_when_form="untyped", done_when="When the work is complete.",
                    deadline=AT + timedelta(days=30))
    await _health(scenario.engine, "signal", "up", unit=UNIT)
    await scenario.item("witness", _TEXT, at=AT)
    await wire.condition_matcher(scenario.engine).run_once()
    [due] = await _due(scenario.engine, UNIT)
    assert (due.graph_id, due.occurrence) == ("waiting", "witness")
