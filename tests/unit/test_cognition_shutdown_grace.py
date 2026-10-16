"""`CognitionShutdown`'s grace: a session already running
is allowed `grace_seconds` to finish, and one still running at the deadline is cancelled
and records its wake `cancelled` with a restart detail, releasing its unit. In-process
and fast; `tests/integration/test_sigterm_releases_the_claim.py` is the same thing under
a real SIGTERM and real Postgres."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from nora.app.cognition_session import CognitionRunResult
from nora.app.cognition_wiring import CognitionShutdown
from nora.domain.stimuli import is_restart_detail
from tests.unit.test_cognition_session_lifecycle import ScriptedAgent, _rig, blocked


async def _finishes_soon(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    agent.started.set()
    await asyncio.sleep(0.15)
    return CognitionRunResult(stop_reason="end_turn")


async def _running(behaviour: Any) -> tuple[Any, ScriptedAgent]:
    agent = ScriptedAgent(script=[behaviour])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()
    return rig, agent


async def test_a_session_that_finishes_inside_the_grace_is_not_cancelled():
    rig, agent = await _running(_finishes_soon)
    await CognitionShutdown(evaluator=rig.ev, runner=rig.runner, grace_seconds=5.0)()
    assert rig.ends() == ["closed"] and not agent.cancelled


async def test_a_session_still_running_at_the_deadline_is_cancelled_as_a_restart_and_released():
    rig, agent = await _running(blocked)
    started = time.monotonic()
    await CognitionShutdown(evaluator=rig.ev, runner=rig.runner, grace_seconds=0.2)()
    elapsed = time.monotonic() - started
    assert 0.18 <= elapsed < 3                                  # it waited the grace, no longer
    assert agent.cancelled and rig.ends() == ["cancelled"]
    outcome = next(iter(rig.stimuli.outcomes.values()))
    assert outcome.cause == "none" and is_restart_detail(outcome.detail)
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert [s.id for s in view.pending] == [1] and view.failures == 0


async def test_no_grace_cancels_at_once_as_it_did_before():
    rig, agent = await _running(blocked)
    started = time.monotonic()
    await CognitionShutdown(evaluator=rig.ev, runner=rig.runner)()
    assert time.monotonic() - started < 1.0
    assert agent.cancelled and rig.ends() == ["cancelled"]


async def test_the_grace_is_spent_once_however_many_times_shutdown_is_asked():
    rig, _ = await _running(blocked)
    shutdown = CognitionShutdown(evaluator=rig.ev, runner=rig.runner, grace_seconds=0.2)
    await shutdown()
    started = time.monotonic()
    await shutdown()
    assert time.monotonic() - started < 0.1 and shutdown.done
