"""Cognition's Dreamer-side face: the adaptor and its executor.

`SubconsciousExecutor` is the wake hook the wake evaluator hands an admitted
wake to (design/cognition-loop/30): it wraps the session's `on_wake` in a
`CognitionSessionAdaptor` and runs it through `DreamerRuntime.run` —
admission, task lifecycle and cancellation belong to the Dreamer; the
session's model call goes through `DreamerRuntime.run_agent`, the one
model-invocation route. The sibling `LiveCognitionAgentRunner` /
`AgentFactory` path is gone rather than renamed.

`CognitionSessionAdaptor` is Cognition as the Dreamer sees it: its `unit_key`
is the Dreamer key, `session_run` the work one slot executes, and
`uses_watermark=False` because Cognition settles its own claim — a
watermark commit would be a second settlement of the same rows. The
envelope members (`inputs`/`accept`/`commit`) exist only so the runtime's
protocol check sees a whole adaptor; `session_run` short-circuits before
any of them can be called.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
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
    """ONE wake's session as a `DreamAdaptor` (node 30's second shape).

    Built per admitted wake by `SubconsciousExecutor`: `session_run` is the
    session's `on_wake` bound to that wake's unit, view and ids, and
    `DreamerRuntime.run` detects it and drives `_run_session_body` instead of
    the envelope fold. `uses_watermark=False` and `store_calls_per_tick=0`
    are not defaults picked for convenience: a session settles its own rows
    (its wake outcome, its carries, its session record), so the runtime's
    envelope commit must never fire, and the runtime's `inputs`/`commit`
    bound must never wrap a call this shape does not make.
    """

    unit_key: str
    session_run: Callable[..., Any]
    name: str = COGNITION_ADAPTOR
    key_kind: str = "unit"
    uses_watermark: bool = False
    objective: str = "cognition"
    envelope_key: str = "cognition"
    tools: frozenset[str] = frozenset()
    store_calls_per_tick: int = 0

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        return ""

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]],
    ) -> Sequence[object]:
        return ()

    async def commit(
        self, *, key: str, proposals: Sequence[Any], now_ms: int,
    ) -> int:
        return 0


class SubconsciousExecutor:
    """Runs an admitted wake as a Dreamer slot (node 30).

    Holds the `DreamerRuntime` and the `CognitionSessionRunner`; the wake
    evaluator holds its `wake_hook` exactly as it held the runner's before —
    the hook's shape is unchanged, what runs inside it is not: the session
    executes inside `runtime.run(adaptor=…)` under the `dream:cognition`
    root span and its metrics, so Cognition's execution, cancellation and
    budgets have the one owner every other subconscious adaptor has.
    """

    def __init__(
        self, *, runtime: DreamerRuntime, runner: CognitionSessionRunner,
    ) -> None:
        self._runtime = runtime
        self._runner = runner

    def __getattr__(self, name: str) -> Any:
        """The session runner's other faces — `replay`, `inspect`, `probe`,
        `with_collaborators`, `agent_runner`, `memory`, `observations` —
        answered by the runner itself: the executor is its Dreamer hook,
        not a second session object, and a backfill or CLI that holds it
        holds the same session it would have held before."""
        return getattr(self.__dict__["_runner"], name)

    @property
    def wake_hook(self) -> WakeHook:
        """The evaluator's `on_wake`: an admitted wake, run through the
        Dreamer. The session body is the runner's `on_wake` itself — the
        claim is already made (the evaluator hands `view`), so the adaptor
        binds it and `run` executes it."""
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
        return await self._runtime.run(adaptor=adaptor, key=unit, batch=())

    async def shutdown(self) -> None:
        """The executor's part of the shutdown contract: the session runner's
        own `shutdown`, called once by `CognitionShutdown` after the
        evaluator's in-flight wait and cancel."""
        await self._runner.shutdown()
