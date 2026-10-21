"""The computer session sweeper, on fakes and a
fake clock: when a session expires and why, that it never ends a job or
deletes anything, and that what it cannot see is never read as «nothing
running»."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.computer_sweeper import ComputerSessionSweeper, SweepRules
from nora.app.runtime import computer_sweep_loop
from nora.domain.computer_leases import ComputerLeases, lease_key
from nora.obs.metrics import Metrics
from nora.testing import FakeComputerEventStore, FakeComputerSessionStore, FakeSandboxRuntime

T0 = datetime(2026, 9, 30, 12, tzinfo=UTC)
SCOPE = "ab" * 16


class Clock:
    def __init__(self) -> None:
        self.at = T0

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def world():
    clock = Clock()
    runtime = FakeSandboxRuntime(clock=clock)
    sessions, events, leases, metrics = (FakeComputerSessionStore(), FakeComputerEventStore(),
                                         ComputerLeases(), Metrics())
    sweeper = ComputerSessionSweeper(
        sessions=sessions, events=events, runtime=runtime, leases=leases,
        rules=SweepRules(), clock=clock, metrics=metrics,
    )
    return clock, runtime, sessions, events, leases, metrics, sweeper


async def _open(sessions, at=T0):
    session, _ = await sessions.continue_or_open(
        platform="signal", conversation_id="g", scope=SCOPE, objective="fit",
        actor="tag", tag_id="t", now=at,
    )
    return session


async def test_an_idle_session_expires_after_the_idle_window_and_not_before(world):
    clock, _, sessions, events, _, metrics, sweeper = world
    s = await _open(sessions)
    clock.at = T0 + timedelta(hours=23, minutes=59)
    assert await sweeper.sweep_once() == 0
    clock.at = T0 + timedelta(hours=24)
    assert await sweeper.sweep_once() == 1
    got = sessions.sessions[s.id]
    assert (got.status, got.expired_reason) == ("expired", "idle")
    [row] = events.events
    assert (row.actor, row.tool, row.status, row.session_id) == (
        "sweeper", "computer_session", "expired", s.id)
    assert row.result["reason"] == "idle"
    assert metrics.get("nora_computer_session_expiries_total", reason="idle") == 1


async def test_the_sweeper_never_deletes_anything(world):
    clock, runtime, sessions, events, _, _, sweeper = world
    await runtime.write_text(scope=SCOPE, path="projects/p/report.html", content="<p>x</p>")
    s = await _open(sessions)
    runtime.start_tmux(SCOPE, "shell", running=False)
    before_files = {k: dict(v) for k, v in runtime.files_by_scope.items()}
    clock.at = T0 + timedelta(days=3)
    assert await sweeper.sweep_once() == 1
    assert runtime.files_by_scope == before_files
    assert list(sessions.sessions) == [s.id]
    assert len(events.events) == 1  # appended, nothing removed
    # Not even an idle tmux shell is ended: expiry is a row and a status.
    assert set(runtime.tmux_by_scope[SCOPE]) == {"shell"}
    assert events.events[0].result["tmux_left"] == ["shell"]
    # No command at all: `run` is the only way the port could remove a file.
    assert runtime.run_calls == []


async def test_a_running_tmux_job_keeps_it_open_then_is_flagged_never_killed(world):
    clock, runtime, sessions, events, _, metrics, sweeper = world
    s = await _open(sessions)
    runtime.start_tmux(SCOPE, "scrape", running=True)
    runtime.start_tmux(SCOPE, "shell", running=False)
    clock.at = T0 + timedelta(days=6, hours=23)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].status == "open"
    clock.at = T0 + timedelta(days=7)
    assert await sweeper.sweep_once() == 1
    assert sessions.sessions[s.id].expired_reason == "running_past_limit"
    assert set(runtime.tmux_by_scope[SCOPE]) == {"scrape", "shell"}
    [row] = events.events
    assert row.result["flagged_running_tmux"] == ["scrape"]
    assert metrics.get("nora_computer_session_expiries_total",
                       reason="running_past_limit") == 1


async def test_it_expires_when_every_held_resource_is_gone(world):
    clock, runtime, sessions, _, _, _, sweeper = world
    s = await _open(sessions)
    runtime.start_tmux(SCOPE, "scrape", running=True)
    clock.at = T0 + timedelta(minutes=5)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].held_tmux == ("scrape",)
    runtime.tmux_by_scope[SCOPE].clear()  # the job ended
    clock.at = T0 + timedelta(minutes=20)
    assert await sweeper.sweep_once() == 0  # still within the idle-after window
    clock.at = T0 + timedelta(minutes=31)
    assert await sweeper.sweep_once() == 1
    assert sessions.sessions[s.id].expired_reason == "jobs_ended"


async def test_a_session_that_never_held_anything_is_not_jobs_ended(world):
    clock, _, sessions, _, _, _, sweeper = world
    s = await _open(sessions)
    clock.at = T0 + timedelta(hours=2)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].status == "open"


@pytest.mark.parametrize("blind", ["tmux_unknown", "down"])
async def test_a_room_it_cannot_see_is_skipped_never_expired(world, blind):
    clock, runtime, sessions, events, _, _, sweeper = world
    s = await _open(sessions)
    setattr(runtime, blind, True)
    clock.at = T0 + timedelta(days=30)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].status == "open" and events.events == []


async def test_expiry_is_a_compare_and_set_a_late_call_keeps_it_open(world):
    clock, runtime, sessions, events, _, _, sweeper = world
    stale = await _open(sessions)
    clock.at = T0 + timedelta(hours=25)
    await _open(sessions, at=clock.at)  # a call lands after the sweeper's read
    state = await runtime.state(scope=SCOPE)
    assert await sweeper._sweep(stale, state, clock.at) is False
    assert sessions.sessions[stale.id].status == "open" and events.events == []


async def test_a_call_holding_the_lease_defers_the_sweep(world):
    clock, _, sessions, _, leases, _, sweeper = world
    s = await _open(sessions)
    held = await leases.acquire(lease_key(s.workspace_scope, s.id), live=True)
    clock.at = T0 + timedelta(days=2)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].status == "open"
    leases.release(held)
    assert await sweeper.sweep_once() == 1


async def test_the_loop_survives_a_failing_sweep_and_counts_it():
    calls = 0
    thrice = asyncio.Event()

    class Boom:
        async def sweep_once(self) -> int:
            nonlocal calls
            calls += 1
            if calls >= 3:
                thrice.set()
            raise RuntimeError("down")

    metrics = Metrics()
    task = asyncio.create_task(computer_sweep_loop(Boom(), metrics, interval_seconds=0.001))
    await asyncio.wait_for(thrice.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert metrics.get("nora_errors_total", stage="computer_sweep") >= 2


# -- processes outside tmux ---------------------------------------------------


async def test_a_nohup_job_with_no_tmux_keeps_the_session_open(world):
    """A `nohup python tick.py &` job has no tmux session, only a process the
    room's user owns. Read as «nothing running», the session would expire while
    the job was still going."""
    clock, runtime, sessions, _, _, _, sweeper = world
    s = await _open(sessions)
    runtime.start_process(SCOPE, "python tick.py", pid=159, started=T0)
    clock.at = T0 + timedelta(days=2)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].status == "open"
    runtime.stop_process(SCOPE, 159)
    assert await sweeper.sweep_once() == 1
    assert sessions.sessions[s.id].expired_reason == "idle"


async def test_a_process_past_the_limit_is_flagged_never_killed(world):
    clock, runtime, sessions, events, _, _, sweeper = world
    await _open(sessions)
    runtime.start_process(SCOPE, "python tick.py", pid=159, started=T0)
    clock.at = T0 + timedelta(days=8)
    assert await sweeper.sweep_once() == 1
    [row] = events.events
    assert row.result["reason"] == "running_past_limit"
    assert row.result["flagged_running_processes"] == [
        {"pid": 159, "command": "python tick.py"}]
    assert [p.pid for p in runtime.processes_by_scope[SCOPE]] == [159]


async def test_unreadable_processes_are_never_read_as_nothing_running(world):
    clock, runtime, sessions, _, _, _, sweeper = world
    s = await _open(sessions)
    runtime.processes_unknown = True
    clock.at = T0 + timedelta(days=2)
    assert await sweeper.sweep_once() == 0
    assert sessions.sessions[s.id].status == "open"
