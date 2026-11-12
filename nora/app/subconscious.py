"""Cognition's Dreamer-side face: the adaptor and its executor.

`SubconsciousExecutor` is the wake hook the wake evaluator hands an admitted
wake to (design/cognition-loop/execution.md and 30): it wraps the session's `on_wake`
in a `CognitionSessionAdaptor` and hands it to `DreamerRuntime.dispatch` —
the subconscious execution boundary. The Dreamer lane admits the unit,
creates the session task and owns it: in-process occupancy, task lifecycle
and cancellation live beside every other subconscious adaptor's. Wake
policy, the durable claim and the session's own settlement stay with their
components — the evaluator decides and records the wake, the session writes
its outcome. The session's model call goes through
`DreamerRuntime.run_agent`, the one model-invocation route; the sibling
`LiveCognitionAgentRunner` / `AgentFactory` path is gone rather than renamed.

`CognitionSessionAdaptor` is Cognition as the Dreamer sees it: its `unit_key`
is the Dreamer key, `session_run` the work one slot executes, and
`uses_watermark=False` because Cognition settles its own claim — a
watermark commit would be a second settlement of the same rows. It is a
`SessionAdaptor`, not an envelope `DreamAdaptor`: `session_run` is the
work, so the envelope members (`inputs`/`accept`/`commit`) are not
declared at all — the session's prompt is assembled inside the session
occasion, not handed to the runtime as `inputs`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nora.agent.dreamer import DreamerRuntime
from nora.domain.cognition_session import COGNITION_ADAPTOR
from nora.domain.stimuli import UnitView, WakeHook

if TYPE_CHECKING:
    from nora.app.cognition_session import CognitionSessionRunner

log = logging.getLogger("nora.app.subconscious")


@dataclass
class CognitionSessionAdaptor:
    """ONE wake's session as a `SessionAdaptor` (node 30's second shape).

    Built per admitted wake by `SubconsciousExecutor`: `session_run` is the
    session's `on_wake` bound to that wake's unit, view and ids, and
    `DreamerRuntime.run` reads it and drives `_run_session_body` instead of
    the envelope fold. `uses_watermark=False` is not a default picked for
    convenience: a session settles its own rows (its wake outcome, its
    carries, its session record), so the runtime's envelope commit must
    never fire. It is NOT an envelope `DreamAdaptor` — it carries none of
    `inputs`/`accept`/`commit`: the session's prompt is assembled inside
    `session_run` by the session occasion, so declaring an `inputs` here
    would name a prompt builder that does not exist.
    """

    unit_key: str
    session_run: Callable[..., Any]
    name: str = COGNITION_ADAPTOR
    objective: str = "cognition"
    uses_watermark: bool = False


class SubconsciousExecutor:
    """Runs an admitted wake as a Dreamer-owned session task (nodes 18, 30).

    Holds the `DreamerRuntime` and the `CognitionSessionRunner`. The wake
    evaluator's hook shape is unchanged; what runs inside it is not: the
    session executes as a lane task under `DreamerRuntime.dispatch`, the
    `dream:cognition` root span and the Dreamer's metrics, so Cognition's
    admission, in-process occupancy, task lifecycle and cancellation have
    the one owner every other subconscious adaptor has. The evaluator's own
    `_live`/`_background` bookkeeping still shadows the hook call itself —
    it observes the hook, it does not own the session.
    """

    def __init__(
        self, *, runtime: DreamerRuntime, runner: CognitionSessionRunner,
    ) -> None:
        self._runtime = runtime
        self._runner = runner

    # -- the Dreamer lane: admission, occupancy, cancellation ----------------

    def in_flight(self, unit: str) -> bool:
        """Whether the lane holds a live session task for `unit` — the
        in-process half of single-flight, asked of its owner."""
        return self._runtime.in_flight(unit)

    async def wait_in_flight(self, timeout_seconds: float) -> None:
        """Shutdown's grace, against the tasks the lane owns."""
        await self._runtime.wait_in_flight(timeout_seconds)

    async def cancel_in_flight(self) -> None:
        """Cancel every admitted session task — the boundary that created
        them is the boundary that cancels them."""
        await self._runtime.cancel_in_flight()

    # -- the evaluator's hook -------------------------------------------------

    @property
    def wake_hook(self) -> WakeHook:
        """The evaluator's `on_wake`: an admitted wake, dispatched through
        the Dreamer lane. The session body is the runner's `on_wake`
        itself — the claim is already made (the evaluator hands `view`),
        so the adaptor binds it and `run` executes it."""
        return self._on_wake

    async def _on_wake(
        self, *, unit: str, view: UnitView, now_ms: int, wake_id: int,
    ) -> Any:
        runner = self._runner

        async def run_session(*, key: str) -> Any:
            return await runner.on_wake(
                unit=key, view=view, now_ms=now_ms, wake_id=wake_id,
            )

        adaptor = CognitionSessionAdaptor(
            unit_key=unit, session_run=run_session,
        )
        task = self._runtime.dispatch(adaptor=adaptor, key=unit)
        if task is None:
            # The lane refused: a session for this unit is already admitted
            # in this process. LOUD, because the wake row just written holds
            # the unit until it expires — this guard exists for exactly the
            # double-dispatch that should never reach it.
            log.error(
                "cognition session for unit %s is already admitted on the "
                "Dreamer lane; second dispatch refused (wake_id=%s)",
                unit, wake_id,
            )
            return None
        # Awaited, not returned: the hook call ends when the lane task ends,
        # so a cancel of either — the lane's own or the evaluator's wrapper —
        # reaches the session once.
        return await task

    # -- the session runner's other faces, stated rather than forwarded -------
    # A backfill or CLI that holds `deps.cognition_runner` holds the same
    # session object it held before; the OFFLINE lanes (`replay`, `inspect`,
    # `probe`, `with_collaborators`) keep their distinct occupancy — they are
    # driven by their caller's task, never admitted on the Dreamer lane.

    async def shutdown(self) -> None:
        """The settlement half of shutdown: the lane's `cancel_in_flight`
        (called by `CognitionShutdown` first) cancels the tasks; this waits
        for the cancelled sessions' outcome writes to land."""
        await self._runner.shutdown()

    @property
    def agent_runner(self) -> Any:
        return self._runner.agent_runner

    @property
    def memory(self) -> Any:
        return self._runner.memory

    @property
    def observations(self) -> Any:
        return self._runner.observations

    @property
    def max_items(self) -> int:
        return self._runner.max_items

    def with_collaborators(self, **changes: Any) -> CognitionSessionRunner:
        return self._runner.with_collaborators(**changes)

    async def replay(self, *, view: UnitView, now_ms: int, historical: bool = False) -> Any:
        return await self._runner.replay(view=view, now_ms=now_ms, historical=historical)

    async def inspect(self, *, view: UnitView, now_ms: int, historical: bool = False) -> Any:
        return await self._runner.inspect(view=view, now_ms=now_ms, historical=historical)

    async def probe(self, *, unit: str, summary: str, now_ms: int, room: Any = None) -> Any:
        return await self._runner.probe(
            unit=unit, summary=summary, now_ms=now_ms, room=room,
        )
