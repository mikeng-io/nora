"""Learning reports read one unit and distinguish empty data from a store error."""

# ruff: noqa: F811 - pytest resolves imported fixture names.
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.memory.adapters.learning_reads import SqlLearningReads
from nora.memory.domain.learning import Efficacy, IntentionClass, LearningMissing, LearningValue
from nora.memory.wire import intention_verifier
from nora.testing.room_slots import FakeRoomSlots
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_condition_matcher import _intention
from tests.infra.test_intention_verify import _DEADLINE, _NOW, _PERSON_EVENT
from tests.infra.test_item_registration import (
    _AT,
    _PERSON,
    _SECOND,
    _UNIT,
    engine,  # noqa: F401
)

_CLASS = IntentionClass("achieve", _PERSON, "identity_event")
_START = _AT - timedelta(minutes=1)
_END = _NOW + timedelta(minutes=1)


async def _ended(engine, id_, ending, *, unit=_UNIT, form="identity_event"):
    value = _PERSON_EVENT if form == "identity_event" else "Generated condition"
    await _intention(engine, id_, form, value, unit=unit, deadline=_DEADLINE)
    async with engine.begin() as conn:
        await conn.execute(sa.update(INTENTION).where(
            INTENTION.c.unit == unit, INTENTION.c.id == id_,
        ).values(state="ended", ending=ending, ended_at=_DEADLINE))


async def _lesson(engine, id_, *, state="live"):
    graph = SqlCognitiveGraph(engine)
    await graph.create(EDGE, {
        "id": f"depends-{id_}", "unit": _UNIT, "basis": "committed", "kind": "edge",
        "state": state, "exposure": "internal", "writer": "fixture", "asserted_at": _AT,
        "type": "rests_on", "src_id": id_, "dst_id": "generated-lesson",
    })


async def _seed(engine):
    graph = SqlCognitiveGraph(engine)
    await graph.create(CLAIM, {
        "id": "generated-lesson", "unit": _UNIT, "kind": "claim", "basis": "inferred",
        "state": "live", "exposure": "internal", "writer": "fixture", "asserted_at": _AT,
        "part": "lesson", "content": {"text": "Generated lesson"},
        "reasoning": "induced", "confidence": "high",
    })
    for id_, ending in (
        ("with-lesson", "achieved"), ("without-lesson", "leaked"), ("unproven", "unknown"),
    ):
        await _ended(engine, id_, ending)
    await _ended(engine, "untyped", "unknown", form="untyped")
    await _ended(engine, "with-lesson", "leaked", unit=_SECOND)
    await _intention(engine, "draft", "identity_event", _PERSON_EVENT, unit=_UNIT)
    draft = (await _rows(engine, INTENTION))["draft"]
    await graph.revise(INTENTION, dict(
        draft, id="revised", replaces="draft", asserted_at=_END,
    ))
    await _lesson(engine, "with-lesson")
    await _lesson(engine, "unproven")
    await _lesson(engine, "revised")
    await _lesson(engine, "without-lesson", state="retired")
    await intention_verifier(
        engine, lambda: _NOW, max_poll_gap_seconds=900, room_slots=FakeRoomSlots({}),
    ).run_once()


async def test_all_learning_reads_use_one_unit_interval_and_their_own_denominator(engine):
    await _seed(engine)
    reads = SqlLearningReads(engine)

    assert await reads.verdict_coverage(_UNIT, _START, _END) == LearningValue(0.5)
    assert await reads.loose_share(_UNIT, _START, _END) == LearningValue(0.25)
    assert await reads.declared_dependence(_UNIT, _START, _END) == LearningValue(3 / 5)
    assert await reads.efficacy(_UNIT, _START, _END) == LearningValue(Efficacy(1.0, 0.0))
    record = await reads.track_record(_UNIT, _START, _END, _CLASS)
    assert isinstance(record, LearningValue)
    assert (record.value.achieved, record.value.dropped, record.value.leaked) == (1, 0, 1)
    assert (record.value.unknown, record.value.achieved_rate) == (1, 0.5)
    assert await reads.track_record(
        _UNIT, _START, _END, IntentionClass("achieve", _PERSON, "untyped"),
    ) == LearningMissing("nothing_found")
    assert await reads.verdict_coverage(_SECOND, _START, _END) == LearningValue(1.0)


@pytest.mark.parametrize("reader", (
    "verdict_coverage", "loose_share", "declared_dependence", "efficacy", "track_record",
))
async def test_an_empty_interval_does_not_produce_a_number(engine, reader):
    await _seed(engine)
    start, end = _END + timedelta(minutes=1), _END + timedelta(minutes=2)
    method = getattr(SqlLearningReads(engine), reader)
    args = (_CLASS,) if reader == "track_record" else ()
    assert await method(_UNIT, start, end, *args) == LearningMissing("nothing_found")


async def test_store_error_makes_each_learning_read_unavailable(engine):
    async def refuse_connection():
        raise OSError("generated connection failure")

    failed = create_async_engine(engine.url, async_creator=refuse_connection)
    try:
        reads = SqlLearningReads(failed)
        args = (_UNIT, _START, _END)
        for method, params in (
            (reads.verdict_coverage, args), (reads.loose_share, args),
            (reads.declared_dependence, args), (reads.efficacy, args),
            (reads.track_record, (*args, _CLASS)),
        ):
            assert await method(*params) == LearningMissing("unavailable")
    finally:
        await failed.dispose()
