"""The daemon's build moves only on the input record's observation frontiers."""

import pytest

from nora.analytics.__main__ import _tick
from nora.analytics.composition import AnalyticsDependencies
from nora.app.source_health import HEALTHY_PROBE_INTERVAL_MS, AdapterHealthRecorder
from nora.domain.ports import AdapterHealth
from nora.infra.sql import create_sql_engine
from nora.infra.sql.source_health import SqlSourceHealthLog
from tests.fakes import FakeClock
from tests.infra.rollup_fixtures import RollupFixture

_START = 1_767_225_600_000
_STEP = HEALTHY_PROBE_INTERVAL_MS


@pytest.fixture
async def record(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        yield RollupFixture(engine)
    finally:
        await engine.dispose()


async def _advance(record, clock):
    deps = AnalyticsDependencies(
        runner=record.runner, subjects=record.subjects, persons=None, metrics=record.metrics,
        metrics_port=0, tick_seconds=120,
    )
    await _tick(deps, {}, {}, clock=clock)
    build = await record.store.current_build()
    assert build is not None
    return build.through_ms


async def test_clock_alone_does_not_advance_an_observed_watermark(record):
    clock = FakeClock(_START / 1000)
    # Discord and presence have never reported. Only Signal participates.
    await record.message("signal", _START, person=record.people[0])
    assert await _advance(record, clock) == _START
    clock.advance(3600)
    assert await _advance(record, clock) == _START


async def test_reported_items_and_steady_healthy_probes_advance_to_the_earliest_frontier(record):
    clock = FakeClock(_START / 1000)
    recorder = AdapterHealthRecorder(
        log=SqlSourceHealthLog(record.engine, registration=record.registration, clock=clock),
        metrics=record.metrics, clock=clock,
    )
    discord = AdapterHealth(platform="discord", connected=True)
    await recorder.sample(discord)
    await record.message("signal", _START, person=record.people[0])
    await record.fix(record.people[0], _START)
    assert await _advance(record, clock) == _START
    clock.advance(_STEP / 1000)
    await recorder.sample(discord)
    await record.message("signal", _START + 2 * _STEP, person=record.people[0])
    await record.fix(record.people[0], _START + 3 * _STEP)
    assert await _advance(record, clock) == _START + _STEP


async def test_a_reported_source_without_a_new_item_or_probe_holds_the_watermark(record):
    await record.message("signal", _START, person=record.people[0])
    await record.message("discord", _START + _STEP, person=record.people[0])
    await record.fix(record.people[0], _START + 2 * _STEP)
    clock = FakeClock(_START / 1000 + 300)
    assert await _advance(record, clock) == _START
    await record.message("discord", _START + 5 * _STEP, person=record.people[0])
    await record.fix(record.people[0], _START + 6 * _STEP)
    assert await _advance(record, clock) == _START


async def test_unhealthy_probes_do_not_advance_the_watermark(record):
    p = record.people[0]
    await record.message("signal", _START, person=p)
    await record.message("discord", _START + _STEP, person=p)
    await record.fix(p, _START + 2 * _STEP)
    clock = FakeClock(_START / 1000 + 300)
    assert await _advance(record, clock) == _START
    await record.health("signal", "", "transition", "down", _START + 3 * _STEP)
    assert await _advance(record, clock) == _START
