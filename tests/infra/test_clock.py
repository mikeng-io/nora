"""Due graph occurrences under an injected clock."""

# ruff: noqa: F811 - pytest resolves imported fixture names.

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from nora.app.graph_observe import graph_observe
from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.ticks import TICK_SECONDS
from nora.infra.sql.cognitive_graph_schema import CLAIM, INTENTION
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.schema import COGNITION_SESSION, STIMULUS
from nora.memory.wire import graph_clock
from nora.obs.metrics import Metrics
from tests.fakes import FakeClock
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_condition_matcher import _intention
from tests.infra.test_item_registration import _PERSON, _ROOM, _UNIT, engine  # noqa: F401

_EVENT = "generated-due"
_INSTANT = datetime(2026, 11, 19, 12, 40, tzinfo=UTC)


def _trigger(at, zone="Asia/Hong_Kong", recurrence=None):
    return {"kind": "time", "at": at.isoformat(), "zone": zone, "recurrence": recurrence}


async def _schedule(engine, kind, at):
    if kind == "valid_to":
        await SqlCognitiveGraph(engine).create(CLAIM, {
            "unit": _UNIT, "id": _EVENT, "kind": "claim", "basis": "inferred",
            "state": "live", "writer": "fixture", "exposure": "internal",
            "origin_room": _ROOM, "persons": [_PERSON], "part": "fact",
            "content": {"text": "Generated claim."}, "valid_to": at,
            "reasoning": "told", "confidence": "low",
        })
        return
    times = {
        "intention_due": {"trigger": _trigger(at)},
        "review_time": {"review_time": at},
        "deadline": {"deadline": at},
    }[kind]
    await _intention(engine, _EVENT, "act_event", {"act_id": "generated-act"}, **times)


def _expected(kind, at):
    return GraphStimulus(
        _UNIT, kind, _EVENT, at, "clock", _ROOM, _PERSON,
        occurrence=at.astimezone(UTC).isoformat(),
    )


@pytest.mark.parametrize("kind", ["intention_due", "review_time", "valid_to", "deadline"])
async def test_each_clock_kind_publishes_once_at_its_authoritative_instant(engine, kind):
    await _schedule(engine, kind, _INSTANT)
    clock = FakeClock(_INSTANT.timestamp() - 1)
    evaluator = graph_clock(engine, lambda: datetime.fromtimestamp(clock(), UTC))
    store = SqlGraphStimuli(engine)
    await evaluator.run_once()
    assert await store.read(_UNIT) == ()
    clock.advance(1)
    await evaluator.run_once()
    await evaluator.run_once()
    assert await store.read(_UNIT) == (_expected(kind, _INSTANT),)


async def test_restart_publishes_each_missed_occurrence_across_dst(engine):
    start = datetime(2026, 3, 7, 13, tzinfo=UTC)
    await _intention(
        engine, _EVENT, "act_event", {"act_id": "generated-act"},
        trigger=_trigger(start, "America/New_York", {"every": "daily", "count": 4}),
    )
    clock = FakeClock(start.timestamp())

    def now():
        return datetime.fromtimestamp(clock(), UTC)

    await graph_clock(engine, now).run_once()
    assert await SqlGraphStimuli(engine).read(_UNIT) == (_expected("intention_due", start),)
    clock.advance(60 * 60 * 48)
    restarted = graph_clock(engine, now)
    await restarted.run_once()
    await restarted.run_once()
    assert await SqlGraphStimuli(engine).read(_UNIT) == tuple(
        _expected("intention_due", at) for at in (
            start, datetime(2026, 3, 8, 12, tzinfo=UTC),
            datetime(2026, 3, 9, 12, tzinfo=UTC),
        )
    )


async def test_deadline_keeps_its_stored_instant_when_display_zone_changes(engine):
    first_fold = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"))
    second_fold = first_fold.replace(fold=1)
    await _schedule(engine, "deadline", second_fold)
    clock = FakeClock(first_fold.timestamp())
    execute = graph_clock(engine, lambda: datetime.fromtimestamp(clock(), ZoneInfo("Asia/Tokyo")))
    await execute.run_once()
    assert await SqlGraphStimuli(engine).read(_UNIT) == ()
    clock.advance(3600)
    await execute.run_once()
    (due,) = await SqlGraphStimuli(engine).read(_UNIT)
    assert due.eligible_at == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert due.kind == "deadline"


async def test_due_detection_runs_without_a_model_pass_or_legacy_time_stimulus(engine):
    deadline = _INSTANT + timedelta(seconds=TICK_SECONDS // 2)
    await _schedule(engine, "deadline", deadline)
    clock = FakeClock(_INSTANT.timestamp())
    observe = graph_observe(engine, lambda: datetime.fromtimestamp(clock(), UTC), Metrics())
    await observe.tick()
    assert await SqlGraphStimuli(engine).read(_UNIT) == ()
    clock.advance(TICK_SECONDS)
    await observe.tick()

    stimuli = await SqlGraphStimuli(engine).read(_UNIT)
    assert [stimulus.kind for stimulus in stimuli] == ["deadline", "verdict"]
    assert stimuli[0] == _expected("deadline", deadline)
    assert stimuli[1].eligible_at == datetime.fromtimestamp(clock(), UTC)
    intention = (await _rows(engine, INTENTION))[_EVENT]
    assert (intention["state"], intention["ending"], intention["ended_at"]) == (
        "ended", "unknown", deadline,
    )
    async with engine.connect() as conn:
        for table in (STIMULUS, COGNITION_SESSION):
            assert await conn.scalar(sa.select(sa.func.count()).select_from(table)) == 0
