"""The conscious/subconscious boundary, node 30's ownership tests.

- **Positive**: an admitted wake reaches the Dreamer — `SubconsciousExecutor`'s
  hook runs `runtime.run` and the session's `on_wake` executes inside it.
- **Negative**: there is no sibling route — the session body is not reachable
  without `DreamerRuntime.run`, and `build_cognition_runner` refuses to wire
  one without the composition's runtime.
- **Cancel once**: a cancelled `runtime.run` propagates `CancelledError`
  through the adaptor into the session, where it is recorded `cancelled` —
  once, by the session's own finish, never degraded to `failed`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nora.agent.dreamer import DreamOutcome, DreamerRuntime
from nora.app.subconscious import CognitionSessionAdaptor, SubconsciousExecutor
from nora.domain.stimuli import UnitView
from nora.obs.metrics import Metrics
from tests.fakes import dreamer_runtime_for


class _Runner:
    """The session runner stand-in: `on_wake` is the work the adaptor's
    `session_run` wraps; `shutdown` is what the executor's shutdown calls."""

    def __init__(self, *, result: Any = None, block: bool = False) -> None:
        self.on_wake = AsyncMock(return_value=result)
        self.shutdown = AsyncMock()
        self._block = block
        if block:
            async def hang(**kw: Any) -> Any:
                await asyncio.sleep(3600)
            self.on_wake = AsyncMock(side_effect=hang)


def _executor(runtime: DreamerRuntime, runner: _Runner) -> SubconsciousExecutor:
    return SubconsciousExecutor(runtime=runtime, runner=runner)


def _runtime(metrics: Metrics | None = None) -> DreamerRuntime:
    return dreamer_runtime_for(
        models=SimpleNamespace(create=lambda: object()),
        retry=None, metrics=metrics or Metrics(),
    )


_VIEW = UnitView(unit="u1", pending=(), last_activity_ms=None, last_wake=None)


async def test_an_admitted_wake_runs_the_session_through_the_dreamer():
    """The positive composition: hook -> runtime.run -> session_run ->
    runner.on_wake, with the unit as the Dreamer key."""
    runner = _Runner(result=SimpleNamespace(stop_reason="end_turn"))
    executor = _executor(_runtime(), runner)
    outcome = await executor.wake_hook(
        unit="u1", view=_VIEW, now_ms=1_700_000_000_000, wake_id=7,
    )
    assert isinstance(outcome, DreamOutcome)
    runner.on_wake.assert_awaited_once_with(
        unit="u1", view=_VIEW, now_ms=1_700_000_000_000, wake_id=7,
    )


async def test_the_session_body_is_not_reachable_without_run():
    """The negative: `runtime.run` is the door — an executor whose runtime
    never dispatches executes nothing, and `build_cognition_runner` refuses
    to wire Cognition without the composition's DreamerRuntime."""
    runner = _Runner()
    calls: list[dict[str, Any]] = []

    class _RecordsDispatch:
        async def run(self, **kw: Any) -> Any:
            calls.append(kw)

    executor = SubconsciousExecutor(
        runtime=_RecordsDispatch(),  # type: ignore[arg-type]
        runner=runner,
    )
    await executor._on_wake(unit="u1", view=_VIEW, now_ms=0, wake_id=1)
    # The ONLY thing the hook did was hand `runtime.run` a session-shaped
    # adaptor: no session work happens anywhere else, so `on_wake` itself
    # was never called — dispatch is the runtime's, not the hook's.
    assert len(calls) == 1
    adaptor = calls[0]["adaptor"]
    assert isinstance(adaptor, CognitionSessionAdaptor)
    assert adaptor.uses_watermark is False
    runner.on_wake.assert_not_awaited()

    from nora.app.cognition_wiring import build_cognition_runner
    with pytest.raises(ValueError, match="DreamerRuntime"):
        build_cognition_runner(
            cfg=SimpleNamespace(), models=None, retry=None, metrics=Metrics(),
            tools=SimpleNamespace(tool_param_names=lambda: {}),
            instructions=None, store=None, stimulus_store=None,
            note_titles=None, room_proactive=None, notes=None, events=None,
            standing=None, reminders=None, ai_peer="nora", reminder_caps=None,
            open_events_in=None, standing_of=None, homes={}, units=(),
            skills=None, grounder=None, render_budget=None,
            surface=SimpleNamespace(wired=frozenset()), clock=lambda: 0.0,
            model_id="m", model_route="r", budget=None, result_caps={},
            # runtime= deliberately absent — the one argument that proves
            # Cognition is hosted, not sibling-launched.
        )


async def test_a_cancelled_session_propagates_cancelled_error_once():
    """Cancel the `runtime.run` task mid-session: the session sees
    `CancelledError` (recorded `cancelled` by its own finish), and
    `_run_session_body` re-raises rather than degrading to `unavailable`."""
    runner = _Runner(block=True)
    executor = _executor(_runtime(), runner)
    task = asyncio.create_task(executor.wake_hook(
        unit="u1", view=_VIEW, now_ms=0, wake_id=9,
    ))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runner.on_wake.await_count == 1


async def test_the_executor_shutdown_is_the_runners_once():
    runner = _Runner()
    executor = _executor(_runtime(), runner)
    await executor.shutdown()
    runner.shutdown.assert_awaited_once()


# -- the canonical tick, node 18 -------------------------------------------------

from datetime import UTC, datetime, timedelta

from nora.domain.ticks import (
    TICK_MS,
    due_tick_of_at,
    due_tick_of_ms,
    tick_of_at,
    tick_of_ms,
    tick_start_ms,
)


def test_a_deadline_rounds_up_and_the_boundary_is_its_own_tick():
    """Fake-clock boundaries: due at 03:30:50 is eligible from the 03:31 tick;
    due at 03:31:00 exactly is eligible IN 03:31. Upward rounding, never early."""
    # one second into a minute -> next tick
    assert due_tick_of_ms(60_001) == 2
    # exactly on the boundary -> that tick
    assert due_tick_of_ms(120_000) == 2
    # the read side floors
    assert tick_of_ms(119_999) == 1
    assert tick_of_ms(120_000) == 2


def test_datetime_and_ms_arithmetic_agree():
    at = datetime(2026, 8, 8, 12, 40, 0, tzinfo=UTC)
    assert tick_of_at(at) == tick_of_ms(120_000 * 0 + int(at.timestamp() * 1000))
    off = at + timedelta(seconds=30)
    assert due_tick_of_at(off) == due_tick_of_ms(int(off.timestamp() * 1000))
    assert tick_start_ms(tick_of_at(at)) == int(at.timestamp() * 1000)


async def test_the_fake_reminder_store_spells_the_same_tick_the_column_computes():
    """Reminder `due()` parity: a row due mid-tick is NOT due at its own
    instant, IS due at the next boundary; a boundary row is due in its tick."""
    from nora.domain.reminders import Reminder, ReminderState
    from nora.testing.reminders import FakeReminderStore
    import uuid

    t0 = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    store = FakeReminderStore()
    mid = Reminder(
        id=uuid.uuid4(), requester_person_id="p", platform="signal",
        conversation_id="c", conversation_type="group", text="mid",
        created_at=t0, due_at=t0 + timedelta(minutes=40, seconds=30),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    edge = Reminder(
        id=uuid.uuid4(), requester_person_id="p", platform="signal",
        conversation_id="c", conversation_type="group", text="edge",
        created_at=t0, due_at=t0 + timedelta(minutes=41),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    await store.create(mid)
    await store.create(edge)
    # At the mid row's own instant it is NOT due (upward rounding).
    assert [r.text for r in await store.due(now=mid.due_at, limit=10)] == []
    # At the next boundary both are: mid's tick is the 12:41 tick, edge's is too.
    boundary = t0 + timedelta(minutes=41)
    assert [r.text for r in await store.due(now=boundary, limit=10)] == ["mid", "edge"]
    # Just before the boundary, neither.
    assert await store.due(now=boundary - timedelta(milliseconds=1), limit=10) == []
    # Long overdue: still exactly the pending set.
    assert {r.id for r in await store.due(now=t0 + timedelta(days=1), limit=10)} == {mid.id, edge.id}
    await store.aclose()
