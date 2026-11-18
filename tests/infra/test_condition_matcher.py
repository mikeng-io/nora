"""Observed witnesses settle only the conditions they prove."""

# ruff: noqa: F811 - pytest resolves imported fixture names.

from dataclasses import replace
from datetime import timedelta
from typing import cast

import pytest
import sqlalchemy as sa

from nora.domain.observations import Observation, Operation, PollPayload, SourceScope
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION, NORA_ACT_OUTCOME
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.schema import MESSAGES, PLATFORM_OBJECT_STATE, ROW_HISTORY, SOURCE_HEALTH
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.domain.acts import Outcome, Verdict
from nora.memory.wire import condition_matcher
from tests.infra.test_act_envelope import _act
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_item_registration import (
    _AT,
    _MS,
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    _Writer,
    engine,  # noqa: F401
)


async def _intention(
    engine, id_, form, value, *, mode="achieve", drop=None, unit=_UNIT, **times,
):
    role = "done_when" if mode == "achieve" else "holds_while"
    graph = SqlCognitiveGraph(engine)
    await graph.create(INTENTION, {
        "id": id_, "unit": unit, "kind": "intention", "basis": "committed",
        "state": "open", "writer": "fixture", "exposure": "internal",
        "origin_room": _ROOM, "owner": "generated-nora", "owed_to": _PERSON,
        "form": mode, role: value, f"{role}_form": form, "asserted_at": _AT,
        "delivery_mode": "choose_moment" if times.get("trigger") else "when_it_comes_up",
        **({"drop_when": drop[1], "drop_when_form": drop[0]} if drop else {}), **times,
    })


async def _health(engine, source, outcome, unit=_UNIT, scope=""):
    async with engine.begin() as conn:
        values = {
            "source": source, "scope": scope, "event": "transition", "outcome": outcome,
            "detail": "", "at_ms": _MS,
        }
        planes = await _registration().planes(
            conn, SOURCE_HEALTH, unit=unit, occurred_at=_AT,
        )
        await conn.execute(sa.insert(SOURCE_HEALTH).values(**values, **planes))


async def _endings(engine):
    return {key: row["ending"] for key, row in (await _rows(engine, INTENTION)).items()}


async def _edges(engine, edge_type="closed_by"):
    return {
        row["src_id"]: row for row in (await _rows(engine, EDGE)).values()
        if row["type"] == edge_type
    }


@pytest.mark.parametrize("verdict", ["done", "failed", "uncertain"])
async def test_only_the_named_done_act_closes_its_intention(engine, verdict):
    act = _act(context=None)
    act = replace(act, authority=replace(act.authority, unit=_UNIT, intention="waiting"))
    store = SqlActStore(engine, _registration())
    await _intention(engine, "waiting", "act_event", {"act_id": act.identity.id})
    await _intention(engine, "unrelated", "act_event", {"act_id": "generated-other"})
    await _health(engine, act.identity.executor, "up")
    assert await store.reserve(act)
    await store.observe(act, Outcome(cast(Verdict, verdict)))
    matcher = condition_matcher(engine)
    await matcher.run_once()
    await matcher.run_once()
    expected = "achieved" if verdict == "done" else None
    assert await _endings(engine) == {"waiting": expected, "unrelated": None}
    (outcome,) = (await _rows(engine, NORA_ACT_OUTCOME)).values()
    edges = await _edges(engine)
    assert list(edges) == (["waiting"] if expected else [])
    if expected:
        assert edges["waiting"]["dst_id"] == outcome["graph_id"]
        assert edges["waiting"]["basis"] == "computed"
    failed = tuple(s for s in await SqlGraphStimuli(engine).read(_UNIT) if s.kind == "failed_act")
    assert tuple(s.graph_id for s in failed) == (
        (outcome["graph_id"],) if verdict == "failed" else ()
    )


async def test_the_explicit_message_must_come_from_the_awaited_person(engine):
    text = "I accept the generated option."
    await _intention(engine, "waiting", "person_event", {
        "person_id": _PERSON, "platform": "signal", "room": _ROOM, "text": text,
    })
    await _health(engine, "signal", "up")
    writer = _Writer(engine, _registration())
    store = SqlRecordStore(engine, registration=writer.registration)
    message = replace(writer._message(), body=text)
    await store.append(message=message, person_id="generated-other-person")
    await store.append(message=replace(message, body="Another topic."), person_id=_PERSON)
    matcher = condition_matcher(engine)
    await matcher.run_once()
    assert await _endings(engine) == {"waiting": None}
    assert await _edges(engine) == {}
    matching = await store.append(message=message, person_id=_PERSON)
    await matcher.run_once()
    assert await _endings(engine) == {"waiting": "achieved"}
    assert (await _edges(engine))["waiting"]["dst_id"] == (
        await _rows(engine, MESSAGES)
    )[matching]["graph_id"]


async def _head(engine, operation: Operation, state: str):
    store = SqlObservationStore(engine, registration=_registration())
    await store.append(
        observation=Observation(
            "poll", SourceScope("signal", "generated-account", _ROOM), "generated-poll",
            operation, PollPayload(options=(("a", "Generated choice"),), state=state),
            occurred_ms=_MS, received_ms=_MS, time_quality="upstream",
        ),
        event_id=state, source_digest=state,
    )
    (head,) = (await _rows(engine, PLATFORM_OBJECT_STATE)).values()
    return head


@pytest.mark.parametrize(
    ("role", "ending"), [("done", "achieved"), ("drop", "dropped"), ("holds", "ceased")],
)
async def test_current_head_revision_settles_the_condition_and_pins_its_witness(
    engine, role, ending,
):
    head = await _head(engine, "create", "open")
    await _health(engine, "signal", "up")
    predicate = {
        "graph_id": head["graph_id"], "path": ["source_state", "state"],
        "equals": "open" if role == "holds" else "closed",
    }
    graph = SqlCognitiveGraph(engine)
    if role == "drop":
        await _intention(engine, role, "act_event", {"act_id": "other"},
                         drop=("predicate", predicate))
    else:
        await _intention(engine, role, "predicate", predicate,
                         mode="maintain" if role == "holds" else "achieve")
    if role == "holds":
        await graph.create(ENTITY, {
            "unit": _UNIT, "id": "generated-watched", "kind": "entity", "basis": "computed",
            "state": "admitted", "writer": "fixture", "exposure": "internal",
            "name": "Generated page",
        })
        await graph.create(EDGE, {
            "id": "generated-watches", "unit": _UNIT, "kind": "edge", "basis": "committed",
            "type": "watches", "state": "live", "writer": "fixture", "exposure": "internal",
            "src_id": role, "dst_id": "generated-watched",
        })
    matcher = condition_matcher(engine)
    await matcher.run_once()
    assert await _endings(engine) == {role: None}
    current = await _head(engine, "revise", "closed")
    table = PLATFORM_OBJECT_STATE
    if role == "holds":
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "ALTER TABLE edge ADD CONSTRAINT generated_reject_close "
                "CHECK (type <> 'closed_by')",
            ))
        with pytest.raises(sa.exc.IntegrityError):
            await matcher.run_once()
        assert await _endings(engine) == {role: None}
        assert (await _edges(engine, "watches"))[role]["state"] == "live"
        async with engine.begin() as conn:
            await conn.execute(sa.text("ALTER TABLE edge DROP CONSTRAINT generated_reject_close"))
    await matcher.run_once()
    assert await _endings(engine) == {role: ending}
    edge = (await _edges(engine))[role]
    assert edge["dst_id"] == current["graph_id"]
    if role == "holds":
        assert (await _edges(engine, "watches"))[role]["state"] == "retired"
    await _head(engine, "revise", "withdrawn")
    async with engine.connect() as conn:
        h = ROW_HISTORY.c
        image = await conn.scalar(sa.select(h.image).where(
            h.table_name == table.name, h.recorded_at == edge["occurred_at"],
        ))
    assert image["source_state"]["state"] == "closed"


async def test_done_wins_and_unhealthy_or_absent_health_is_unknown(engine):
    head = await _head(engine, "create", "open")
    value = {"graph_id": head["graph_id"], "path": ["source_state", "state"], "equals": "open"}
    await _intention(engine, "waiting", "predicate", value, drop=("predicate", value))
    matcher = condition_matcher(engine)
    await matcher.run_once()
    assert await _endings(engine) == {"waiting": None}
    await _health(engine, "signal", "down")
    await matcher.run_once()
    assert await _endings(engine) == {"waiting": None}
    await _health(engine, "signal", "up")
    await matcher.run_once()
    assert await _endings(engine) == {"waiting": "achieved"}


async def test_deadline_and_untyped_intentions_are_left_for_verify(engine):
    await _intention(
        engine, "left-open", "untyped", "generated condition",
        deadline=_AT + timedelta(minutes=1), drop=("deadline", _AT.isoformat()),
    )
    text = "I confirm."
    await _intention(engine, "positive-control", "person_event", {
        "person_id": _PERSON, "platform": "signal", "room": _ROOM, "text": text,
    })
    await _health(engine, "signal", "up")
    writer = _Writer(engine, _registration())
    await SqlRecordStore(engine, registration=writer.registration).append(
        message=replace(writer._message(), body=text), person_id=_PERSON,
    )
    await condition_matcher(engine).run_once()
    assert await _endings(engine) == {"left-open": None, "positive-control": "achieved"}
    assert set(await _edges(engine)) == {"positive-control"}
