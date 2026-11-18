"""The real silence deviation can assert graph absence only with proved coverage."""

import pytest
import sqlalchemy as sa

from nora.analytics.units import UnitConfig
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import ENTITY
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.schema import STIMULUS
from tests.infra.rollup_fixtures import RollupFixture

_START = 1_767_312_000_000
_SIX_HOURS = 21_600_000
_END = _START + 4 * _SIX_HOURS


@pytest.fixture
async def record(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        yield RollupFixture(engine, config=UnitConfig(
            deviation_silence_days=1, tz_name="UTC",
            health_max_poll_gap_seconds=_SIX_HOURS / 1000,
        ))
    finally:
        await engine.dispose()


async def _silent_span(record, *, first_at_start=None, outage=False):
    await record.message("signal", _START - _SIX_HOURS, person=record.people[0])
    for platform, _ in record.rooms:
        start = _START if first_at_start == platform else _START - 1_000
        await record.health(platform, "", "transition", "up", start)
    if first_at_start != "presence":
        await record.health("presence", record.circle, "poll", "ok", _START - 1_000)
    for step in range(5):
        await record.health("presence", record.circle, "poll", "ok", _START + step * _SIX_HOURS)
    if outage:
        await record.health("discord", "", "transition", "down", _START + _SIX_HOURS)
        await record.health("discord", "", "transition", "up", _START + 2 * _SIX_HOURS)


async def _run(record):
    build = await record.runner.ensure_build(through_ms=_END)
    await record.runner.run(build_id=build, through_ms=_END)


async def _absences(record):
    return tuple(
        s for s in await SqlGraphStimuli(record.engine).read(record.unit) if s.kind == "absence"
    )


async def _legacy(record):
    async with record.engine.connect() as conn:
        return (await conn.scalars(sa.select(STIMULUS.c.id).where(
            STIMULUS.c.unit == record.unit, STIMULUS.c.item == "deviation_silence",
        ))).all()


async def test_covered_silence_produces_one_absence_anchored_to_the_person(record):
    await _silent_span(record)
    await _run(record)
    absence, = await _absences(record)
    async with record.engine.connect() as conn:
        pointer, = (await conn.execute(sa.select(ENTITY).where(
            ENTITY.c.id == absence.graph_id, ENTITY.c.unit == record.unit,
        ))).mappings()
    assert (pointer["pointer_kind"], pointer["pointer_ref"], absence.person) == (
        "person", record.people[0], record.people[0],
    )
    assert len(await _legacy(record)) == 1


async def test_one_unhealthy_row_anywhere_in_the_span_prevents_absence(record):
    await _silent_span(record, outage=True)
    await _run(record)
    assert await _absences(record) == ()
    # The rebuild independently sees the same silent span. Only the missing
    # proof should keep it from reaching the stimulus store.
    assert (await record.store.current_build()).through_ms == _END
    assert await _legacy(record) == []


@pytest.mark.parametrize("source", ["presence", "discord"])
async def test_health_must_precede_the_span_even_when_the_legacy_wake_can_raise(record, source):
    await _silent_span(record, first_at_start=source)
    await _run(record)
    assert len(await _legacy(record)) == 1
    assert await _absences(record) == ()


async def test_rebuilding_and_revisiting_the_same_span_keeps_the_absence_identity(record):
    await _silent_span(record)
    await _run(record)
    before, = await _absences(record)
    rebuilt = await record.runner.rebuild(through_ms=_END)
    await record.runner.run(build_id=rebuilt, through_ms=_END)
    assert await _absences(record) == (before,)
    assert len(await _legacy(record)) == 1
