"""Lifetime concerns `Handler` and `App` used to own directly (Phase 3, Task 7).

This module owns background-task lifetime: the strong-reference task set every
fire-and-forget spawn is tracked in, the retention prune sweep, the `Debounce`
TTL sweep, and per-adapter restart-with-backoff supervision. `nora/handler.py`
is deleted; `nora/__main__.py:103-120` wires all four units through
`nora.app.composition.build_dependencies`, which is what actually constructs
them for production traffic. It takes **dependencies, not config**: the
composition root passes a store, metrics, adapters and intervals, never
`Config` itself (`nora.app` is one of only two packages allowed to read it —
see `nora/app/composition.py` — and `runtime.py` deliberately isn't one of
them).

Four responsibilities, four independent units below, none of which know about
each other:

- `BackgroundTasks` — the strong-reference set `Handler._spawn` used to hold,
  now generic.
- `prune_loop` — moved from `nora.__main__._prune_loop`, unchanged in behaviour.
- `debounce_sweep_loop` — new: keeps `Debounce._last` bounded (see `nora/tag.py`'s
  `sweep`).
- `supervise_adapter` / `start_adapters` — per-adapter restart-with-backoff,
  replacing what would otherwise be a bare `asyncio.gather`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .. import tag as tag_module
from ..obs.metrics import Metrics

log = logging.getLogger("nora.app.runtime")


class BackgroundTasks:
    """Strong-reference holder for fire-and-forget tasks (carried over from
    `Handler._spawn`/`Handler._done`, verbatim behaviour).

    asyncio's event loop keeps only a *weak* reference to a task it did not
    hand back to anyone; a bare `asyncio.create_task(...)` whose return value
    is discarded can be garbage-collected mid-await. This set is what keeps
    every spawned coroutine alive until it finishes on its own.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task failed: %s", task.exception())

    def __len__(self) -> int:
        return len(self._tasks)

    async def cancel_all(self) -> None:
        """Best-effort shutdown: cancel every tracked task and wait for it to unwind.

        Discards each task from `_tasks` itself once its `await` returns, rather
        than relying solely on `_on_done` (registered at `spawn` time) to have
        already fired: a done-callback added via `add_done_callback` is delivered
        through `loop.call_soon`, and is not guaranteed to have run by the moment
        an `await` on that same task returns here — pre-existing gap this method
        found failing (`tests/unit/test_runtime.py::
        test_cancel_all_cancels_every_tracked_task`, reproduced against the
        unmodified Task 7 commit) before this fix, unrelated to Phase 3 Task 8's
        own scope but tightened alongside it since `App.shutdown` now calls this.
        `discard`, not `remove`: `_on_done` may still race ahead and already have
        removed the task, and discarding an already-absent element is a no-op.
        """
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._tasks.discard(task)


class _PrunableStore(Protocol):
    """The one method `prune_loop` needs. `AuditStore` (the domain port)
    deliberately has no `prune` — see `nora/domain/ports.py`'s module docstring:
    retention policy is owned by the runtime, not something a stage programs
    against through the port. This narrow local shape mirrors that call-out
    (same pattern as `nora.ingest.stages.parse.ParsingAdapter`)."""

    async def prune_all(self) -> int: ...


async def prune_loop(store: _PrunableStore, metrics: Metrics, *, interval_seconds: float) -> None:
    """The retention sweep `append` no longer does inline. Moved from
    `nora.__main__._prune_loop` unchanged: sleep, prune, log a non-zero count,
    and on failure log + count `nora_errors_total{stage=prune}` and keep looping
    — a failing sweep must never take the loop, or the process, down with it.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            deleted = await store.prune_all()
            if deleted:
                log.info("pruned %d rows past the retention window", deleted)
        except Exception:
            log.exception("prune sweep failed")
            metrics.inc("nora_errors_total", stage="prune")


async def debounce_sweep_loop(
    debounce: tag_module.Debounce,
    metrics: Metrics,
    *,
    interval_seconds: float,
    ttl_seconds: float,
) -> None:
    """Keeps `Debounce._last` bounded: `nora/tag.py:63` grows one entry per
    distinct (platform, conversation_id) forever otherwise, now scaling with
    platforms x conversations. Same failure discipline as `prune_loop` — a
    failing sweep is logged and counted, never fatal to the loop."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            dropped = debounce.sweep(ttl_seconds)
            if dropped:
                log.info("debounce sweep dropped %d stale entries", dropped)
        except Exception:
            log.exception("debounce sweep failed")
            metrics.inc("nora_errors_total", stage="debounce_sweep")


@dataclass(frozen=True)
class SupervisedAdapter:
    """A platform name paired with a zero-arg coroutine factory that runs its
    listen loop.

    Deliberately not typed against a `PlatformAdapter` shape: today's
    production `SignalAdapter` (`nora.platform.base`) takes a positional
    `on_event` dict callback, and the target port
    (`nora.domain.ports.PlatformAdapter`) takes a keyword-only `on_message`
    callback over `NormalizedMessage` — the two do not agree yet (tracked by
    `tests/contract/test_port_conformance.py`), and this module has no
    business picking a side. The composition root binds whichever shape is
    live at the call site, e.g. `functools.partial(adapter.listen, on_event)`.
    """

    platform: str
    listen: Callable[[], Awaitable[None]]


async def supervise_adapter(
    adapter: SupervisedAdapter,
    metrics: Metrics,
    *,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
) -> None:
    """Runs `adapter.listen()` forever, restarting with capped exponential
    backoff on any failure.

    A naive `asyncio.gather` over several adapters either takes the whole
    process down on one adapter's bug, or — if that's guarded against with a
    blanket try/except around the gather — hides a dead listener behind a
    still-healthy `/health`. This keeps a failure scoped to its one platform,
    counted, and retried, so the other adapters and the process both survive
    it and the failure is observable.
    """
    delay = base_delay_seconds
    while True:
        try:
            await adapter.listen()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("adapter %s listen() failed; restarting", adapter.platform)
            metrics.inc("nora_adapter_restarts_total", platform=adapter.platform)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay_seconds)


def start_adapters(
    background: BackgroundTasks,
    adapters: Sequence[SupervisedAdapter],
    metrics: Metrics,
) -> list[asyncio.Task[None]]:
    """Spawns one supervised task per adapter, tracked by `background` like
    every other fire-and-forget coroutine — the strong-reference requirement
    applies here too."""
    return [background.spawn(supervise_adapter(a, metrics)) for a in adapters]
