"""Verify records one citable verdict and stimulus for each ended achieve head."""

# ruff: noqa: F811 - pytest resolves imported fixture names.
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.app.graph_observe import graph_observe
from nora.config import AnalyticsConfig
from nora.infra.sql.cognitive_graph_schema import EDGE, INTENTION, INTENTION_VERDICT
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.schema import MESSAGES
from nora.memory.app.admission import Stored
from nora.memory.domain.admission_proposals import Retire, Scope
from nora.memory.wire import condition_matcher, intention_verifier
from nora.testing.room_slots import FakeRoomSlots
from tests.infra.test_cognitive_graph_admission import (
    CLAIM_PROPOSAL,
    PASS,
    _rows,
    _service,
    engine,  # noqa: F401
)
from tests.infra.test_condition_matcher import _health, _intention
from tests.infra.test_intention_verify import _Errors
from tests.infra.test_item_registration import _AT

_DEADLINE = _AT + timedelta(minutes=10)
_CONDITION = {"person_id": "fixture-person", "platform": "signal", "room": "room-1", "text": "Yes."}


async def _tick(engine, now):
    await intention_verifier(
        engine, lambda: now, max_poll_gap_seconds=900, room_slots=FakeRoomSlots({}),
    ).run_once()


async def _verdicts(engine):
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(INTENTION_VERDICT))
        return {row["intention_id"]: dict(row) for row in result.mappings()}


async def _stimuli(engine):
    return tuple(s for s in await SqlGraphStimuli(engine).read("u") if s.kind == "verdict")


async def test_every_ended_achieve_head_has_one_verdict_and_stimulus(engine):
    for id_ in ("achieved", "leaked", "before-drop"):
        await _intention(
            engine, id_, "person_event", _CONDITION, unit="u", origin_room="signal:room-1",
            deadline=_DEADLINE,
        )
    await _intention(
        engine, "unknown", "untyped", "Generated condition", unit="u", deadline=_DEADLINE,
    )
    await _intention(
        engine, "maintain", "untyped", "Generated condition", unit="u", mode="maintain",
        drop=("deadline", _DEADLINE.isoformat()),
    )
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.graph_id == "item-1").values(
            body=_CONDITION["text"], asserted_at=_AT + timedelta(seconds=5),
            excluded_persons=["generated-witness-exclusion"],
        ))
        await conn.execute(sa.update(INTENTION).where(INTENTION.c.id == "leaked").values(
            done_when={**_CONDITION, "text": "Generated unmatched reply"},
        ))
    await _health(engine, "signal", "up", unit="u", at=_AT - timedelta(minutes=1))
    await _health(engine, "signal", "up", unit="u", at=_AT + timedelta(minutes=1))
    retire = Retire(
        id="dropped", base="before-drop", scope=Scope("u", exposure=None),
        basis="committed", ending="dropped",
    )
    result = await _service(engine).admit((retire,), PASS, ("before-drop",))
    assert result.outcomes == (Stored("dropped", "before-drop"),)
    await condition_matcher(engine).run_once()

    now = datetime.now(UTC)
    await _tick(engine, now)

    verdicts = await _verdicts(engine)
    assert {key: row["ending"] for key, row in verdicts.items()} == {
        ending: ending for ending in ("achieved", "dropped", "leaked", "unknown")
    }
    assert verdicts["achieved"]["witness_id"] == "item-1"
    assert verdicts["achieved"]["excluded_persons"] == ["generated-witness-exclusion"]
    assert verdicts["achieved"]["condition_form"] == "person_event"
    assert verdicts["leaked"]["witness_id"] is None
    assert verdicts["leaked"]["ended_at"] == _DEADLINE
    assert (await _rows(engine, INTENTION))["maintain"]["ending"] == "dropped"
    stimuli = await _stimuli(engine)
    assert len(stimuli) == 4
    by_id = {row["graph_id"]: row for row in verdicts.values()}
    assert {s.graph_id for s in stimuli} == set(by_id)
    for s in stimuli:
        row = by_id[s.graph_id]
        assert (s.source, s.occurrence, s.eligible_at, s.room, s.person) == (
            "verify", "", now, row["origin_room"], row["owed_to"],
        )

    await _tick(engine, now + timedelta(minutes=1))

    assert await _verdicts(engine) == verdicts
    assert await _stimuli(engine) == stimuli


async def _clock_end(engine, *, id_="due", **scope):
    await _intention(
        engine, id_, "untyped", "Generated condition", unit="u",
        deadline=_DEADLINE, origin_room="signal:room-1", **scope,
    )


async def test_verdict_is_citable_as_an_aggregate_in_admission(engine):
    await _clock_end(engine)
    await _tick(engine, datetime.now(UTC))
    verdict = (await _verdicts(engine))["due"]
    proposal = replace(
        CLAIM_PROPOSAL, citations=(verdict["graph_id"],), scope=Scope("u", exposure=None),
    )

    result = await _service(engine).admit((proposal,), PASS, (verdict["graph_id"],))

    assert result.outcomes == (Stored("claim-1"),)
    edges = (await _rows(engine, EDGE)).values()
    assert [(e["type"], e["dst_id"]) for e in edges] == [("cites", verdict["graph_id"])]


async def test_operator_verdict_is_rebuilt_without_publishing_a_stimulus(engine):
    await _clock_end(
        engine, id_="operator-only", exposure="operator", excluded_persons=["generated-exclusion"],
    )
    await _clock_end(engine)
    await _tick(engine, datetime.now(UTC))
    expected = await _verdicts(engine)
    assert expected["operator-only"]["exposure"] == "operator"
    assert expected["operator-only"]["excluded_persons"] == ["generated-exclusion"]
    assert {s.graph_id for s in await _stimuli(engine)} == {expected["due"]["graph_id"]}
    async with engine.begin() as conn:
        await conn.execute(sa.delete(INTENTION_VERDICT))

    await _tick(engine, datetime.now(UTC) + timedelta(minutes=1))

    assert await _verdicts(engine) == expected
    assert {s.graph_id for s in await _stimuli(engine)} == {expected["due"]["graph_id"]}


@pytest.mark.fresh_database
async def test_verdict_and_stimulus_rollback_together_on_publish_failure(engine):
    await _clock_end(engine)
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "ALTER TABLE graph_stimulus ADD CONSTRAINT fixture_refuse_verdict "
            "CHECK (kind <> 'verdict')"
        ))

    errors = _Errors()
    observe = graph_observe(
        engine, lambda: now, errors, analytics=AnalyticsConfig(), room_slots=FakeRoomSlots({}),
    )
    await observe.tick()

    assert errors.stages == ["intention_verify"]
    assert await _verdicts(engine) == {}
    assert await _stimuli(engine) == ()
    assert (await _rows(engine, INTENTION))["due"]["state"] == "open"
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "ALTER TABLE graph_stimulus DROP CONSTRAINT fixture_refuse_verdict"
        ))
    await _tick(engine, now)
    assert len(await _verdicts(engine)) == len(await _stimuli(engine)) == 1
