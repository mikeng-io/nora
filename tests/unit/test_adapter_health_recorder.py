"""`AdapterHealthRecorder` — platform adapter health as Record, one row per
state TRANSITION."""

from __future__ import annotations

from nora.app.source_health import AdapterHealthRecorder
from nora.domain.ports import AdapterHealth
from nora.obs.metrics import Metrics
from nora.testing.stimuli import InMemorySourceHealthLog


def _recorder(log=None):
    log = log if log is not None else InMemorySourceHealthLog()
    return AdapterHealthRecorder(log=log, metrics=Metrics(), clock=lambda: 1_790_000_000.0), log


async def test_the_first_sample_and_every_change_are_recorded_and_a_steady_state_is_not():
    rec, log = _recorder()
    up = AdapterHealth(platform="signal", connected=True)
    down = AdapterHealth(platform="signal", connected=False)
    for health in (up, up, up, down, down, up):
        await rec.sample(health)
    assert log.outcomes("signal") == ["up", "down", "up"]
    assert {(r.event, r.scope) for r in log.rows} == {("transition", "")}


async def test_degraded_is_its_own_state_with_its_reason():
    rec, log = _recorder()
    await rec.sample(AdapterHealth(platform="instagram", connected=True,
                                    degraded=True, detail="checkpoint"))
    assert [(r.outcome, r.detail) for r in log.rows] == [("degraded", "checkpoint")]


async def test_a_health_read_that_raised_is_unknown_not_down():
    """R-27: «could not check» is not «down»."""
    rec, log = _recorder()
    await rec.unknown("discord")
    assert log.outcomes("discord") == ["unknown"]


async def test_platforms_are_tracked_apart():
    rec, log = _recorder()
    await rec.sample(AdapterHealth(platform="signal", connected=True))
    await rec.sample(AdapterHealth(platform="discord", connected=True))
    assert [(r.source, r.outcome) for r in log.rows] == [("signal", "up"), ("discord", "up")]


async def test_a_failed_write_is_retried_on_the_next_sample():
    log = InMemorySourceHealthLog()
    rec, _ = _recorder(log)
    log.fail = True
    await rec.sample(AdapterHealth(platform="signal", connected=False))
    log.fail = False
    await rec.sample(AdapterHealth(platform="signal", connected=False))
    assert log.outcomes("signal") == ["down"]


async def test_a_connected_socket_that_delivers_nothing_is_stale_not_up():
    """The PlatformSilent threshold: connected, but no event for 900 s."""
    rec, log = _recorder()
    await rec.sample(AdapterHealth(platform="signal", connected=True,
                                   last_event_age_seconds=30))
    await rec.sample(AdapterHealth(platform="signal", connected=True,
                                   last_event_age_seconds=901))
    await rec.sample(AdapterHealth(platform="signal", connected=True,
                                   last_event_age_seconds=5))
    assert log.outcomes("signal") == ["up", "stale", "up"]


async def test_a_hung_write_times_out_and_is_retried():
    """A write that never returns must not freeze the sampler (it publishes
    the liveness gauges too): bounded, counted, retried next sample."""
    import asyncio

    class _Hung(InMemorySourceHealthLog):
        hang = True

        async def record(self, **kwargs):
            if self.hang:
                await asyncio.Event().wait()
            return await super().record(**kwargs)

    log = _Hung()
    metrics = Metrics()
    rec = AdapterHealthRecorder(log=log, metrics=metrics, timeout_seconds=0.01)
    await asyncio.wait_for(rec.sample(AdapterHealth(platform="signal", connected=True)), 1)
    assert log.rows == []
    assert metrics.get("nora_errors_total", stage="source_health", platform="signal") == 1
    log.hang = False
    await rec.sample(AdapterHealth(platform="signal", connected=True))
    assert log.outcomes("signal") == ["up"]
