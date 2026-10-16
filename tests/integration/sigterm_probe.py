"""A stand-in nora process for `test_sigterm_releases_the_claim.py`: the REAL
`nora.__main__.run_until_stopped`, the real wake loop, evaluator and session runner and
`CognitionShutdown`, over REAL Postgres, with only the model faked. Not collected
(no `test_` prefix); the test starts it as a subprocess and sends it a real SIGTERM.

    DATABASE_URL=... PROBE_MODE=block|finish PROBE_GRACE=1.5 [PROBE_LEGACY=1] \
        python -m tests.integration.sigterm_probe

It prints `READY` once a session is running, `STOPPED` when `shutdown` has finished, and
exits 0 when it was stopped by a signal, as `nora` does.

`PROBE_LEGACY=1` runs `main()` as it was before `run_until_stopped` existed (no signal
handler), as the control. Modes: `block` is a session that never finishes (the grace
elapses and it is cancelled);
`finish` is one that ends 0.4 s after it starts (inside the grace, so it is allowed to).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from typing import Any

from nora.__main__ import run_until_stopped
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.app.cognition_wiring import CognitionShutdown
from nora.app.wake import WakeEvaluator, wake_loop
from nora.domain.cognition_session import Room
from nora.domain.stimuli import NewStimulus, WakePolicy
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.obs.metrics import Metrics
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.cognition_store import InMemoryCognitionStore

UNIT = "probe"
POLICY = WakePolicy(
    quiet_seconds=0, cue_quiet_seconds=0, batch_max=1000,
    max_age_seconds=3600, cooldown_seconds=300, max_concurrent_sessions=2,
    session_seconds=60, session_timeout_seconds=900,
)


class _Model:
    """The fake model: the session starts, and then it blocks or finishes."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.started = asyncio.Event()

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any], budget: AdaptorBudget,
        trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        self.started.set()
        await asyncio.sleep(0.4 if self.mode == "finish" else 3600)
        return CognitionRunResult(stop_reason="end_turn")


async def _none(*args: Any, **kwargs: Any) -> Any:
    return None


async def _empty(*args: Any, **kwargs: Any) -> Any:
    return ()


class _App:
    """What `nora.__main__.App` is to `run_until_stopped`: `run()` serves, `shutdown()`
    stops the sessions first and then closes the store, in `App.shutdown`'s order."""

    def __init__(self, evaluator: WakeEvaluator, shutdown: CognitionShutdown,
                 model: _Model, engine: Any, metrics: Metrics) -> None:
        self._evaluator, self._cognition_shutdown = evaluator, shutdown
        self._model, self._engine, self._metrics = model, engine, metrics

    async def run(self) -> None:
        async def announce() -> None:
            await self._model.started.wait()
            print("READY", flush=True)

        pulse = asyncio.ensure_future(announce())
        try:
            await wake_loop(self._evaluator, self._metrics, tick_seconds=0.05)
        finally:
            pulse.cancel()

    async def shutdown(self) -> None:
        await self._cognition_shutdown()
        await self._engine.dispose()
        print("STOPPED", flush=True)


async def main() -> None:
    mode = os.environ.get("PROBE_MODE", "block")
    grace = float(os.environ.get("PROBE_GRACE", "1.5"))
    engine = create_sql_engine(os.environ["DATABASE_URL"])
    stimuli, ledger, metrics, model = (
        SqlStimulusStore(engine), InMemoryCognitionStore(), Metrics(), _Model(mode),
    )
    runner = CognitionSessionRunner(
        store=ledger, stimulus_store=stimuli, agent_runner=model,
        home_rooms={UNIT: Room("signal", "home")}, read_tools_for=lambda unit, rooms: [],
        read_only_tools=frozenset(), skill_text_for=lambda kinds: "", grounder=EmptyGrounder(),
        doctrine_text="DOCTRINE", doctrine_sha256="sha", open_note_titles=_empty,
        open_events=_empty, prior_standing=_none,
        budget=AdaptorBudget(turns=4, total_tokens=1000, output_tokens=100, timeout_seconds=600),
        cooldown_seconds=300.0, units=(UNIT,), session_timeout_seconds=900.0, max_items=12,
        max_strikes=3, metrics=metrics,
    )
    evaluator = WakeEvaluator(
        store=stimuli, units=lambda: (UNIT,), policy=POLICY, metrics=metrics, settle_seconds=0,
        on_wake=runner.wake_hook,
    )
    now = int(time.time() * 1000)
    await stimuli.append(stimulus=NewStimulus(
        unit=UNIT, kind="message", item="message", ref="messages:1", at_ms=now - 60_000,
        platform="signal", conversation_id="home",
    ), recorded_at_ms=now - 60_000)
    app = _App(
        evaluator, CognitionShutdown(evaluator=evaluator, runner=runner, grace_seconds=grace),
        model, engine, metrics,
    )
    if os.environ.get("PROBE_LEGACY") == "1":
        # `main()` without `run_until_stopped`: no handler. The control that shows
        # the harness can see the defect: SIGTERM kills this one without a trace.
        try:
            await app.run()
        finally:
            await app.shutdown()
    else:
        await run_until_stopped(app)


if __name__ == "__main__":
    asyncio.run(main())
