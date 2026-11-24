"""Background task ownership and daemon maintenance loops."""

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
    """Strong-reference holder for fire-and-forget tasks.

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
        an `await` on that same task returns here
        (`tests/unit/test_runtime.py::test_cancel_all_cancels_every_tracked_task`).
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
    """The one method `prune_loop` needs. `RecordStore` (the domain port)
    deliberately has no `prune` — see `nora/domain/ports.py`'s module docstring:
    retention policy is owned by the runtime, not something a stage programs
    against through the port. This narrow local shape mirrors that call-out
    (same pattern as `nora.ingest.stages.audit`'s narrow use of `RecordStore`)."""

    async def prune_all(self) -> int: ...


async def prune_loop(store: _PrunableStore, metrics: Metrics, *, interval_seconds: float) -> None:
    """The retention sweep: sleep, prune, log a non-zero count,
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


class _DrainsIndexQueue(Protocol):
    """The one method `memory_drain_loop` needs
    (`nora.infra.sql.audit.SqlRecordStore.drain_index_queue`)."""

    async def drain_index_queue(self) -> int: ...


class _EmbedsIndexNodes(Protocol):
    """The one method the drain loop's embedding step needs
    (`nora.infra.sql.memory_embed.MemoryEmbedWorker.run_once`)."""

    async def run_once(self) -> int: ...


async def memory_drain_loop(
    store: _DrainsIndexQueue, metrics: Metrics, *, interval_seconds: float,
    embed: _EmbedsIndexNodes | None = None,
) -> None:
    """Re-project what the memory index queued, `prune_loop`'s shape: sleep, drain, and on
    failure log + count `nora_errors_total{stage=memory_drain}` and keep
    looping. The analytics process drains on its own tick; this one covers a
    deployment that runs no analytics. It runs during a `nora memory
    rebuild` too: a drain calls the projector a rebuild calls, which writes
    the source's current row, so the two converge whichever lands last.

    `embed` (`[memory.index] embed`) is the
    embedding worker: one batch after each drain, so a node re-projected
    this tick is embedded on it. Its failure is its own
    (`nora_errors_total{stage=memory_embed}`) and never stops the drain."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            tried = await store.drain_index_queue()
            if tried:
                log.info("memory index drain re-projected %d queued sources", tried)
        except Exception:
            log.exception("memory index drain failed")
            metrics.inc("nora_errors_total", stage="memory_drain")
        if embed is None:
            continue
        try:
            embedded = await embed.run_once()
            if embedded:
                log.info("memory index embedding tried %d nodes", embedded)
        except Exception:
            log.exception("memory index embedding failed")
            metrics.inc("nora_errors_total", stage="memory_embed")


class _SweepsComputerSessions(Protocol):
    """The one method `computer_sweep_loop` needs
    (`nora.app.computer_sweeper.ComputerSessionSweeper`)."""

    async def sweep_once(self) -> int: ...


async def computer_sweep_loop(
    sweeper: _SweepsComputerSessions, metrics: Metrics, *, interval_seconds: float,
) -> None:
    """Expire idle computer sessions, `prune_loop`'s
    shape and discipline: sleep, sweep, log a non-zero count, and on failure
    log + count `nora_errors_total{stage=computer_sweep}` and keep looping.
    Started only while `[computer] enabled`. Expiry never deletes anything;
    `ComputerSessionSweeper` says what it does."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            expired = await sweeper.sweep_once()
            if expired:
                log.info("expired %d computer sessions", expired)
        except Exception:
            log.exception("computer session sweep failed")
            metrics.inc("nora_errors_total", stage="computer_sweep")


class _TicksComputerQueue(Protocol):
    """The one method `computer_queue_loop` needs
    (`nora.app.computer_queue.ComputerQueueRunner`)."""

    async def tick(self) -> int: ...


async def computer_queue_loop(
    runner: _TicksComputerQueue, metrics: Metrics, *, tick_seconds: float = 1.0,
) -> None:
    """Grant the scope FIFOs and sweep dead grants
    (`nora.app.computer_queue.ComputerQueueRunner`), `prune_loop`'s
    discipline: tick, and on failure log + count
    `nora_errors_total{stage=computer_queue}` and keep looping. Started only
    while `[computer.scoped] enabled`."""
    while True:
        try:
            await runner.tick()
        except Exception:
            log.exception("computer queue tick failed")
            metrics.inc("nora_errors_total", stage="computer_queue")
        await asyncio.sleep(tick_seconds)


class _SweepsBrowserTabs(Protocol):
    """The one method `computer_browser_sweep_loop` needs
    (`nora.infra.computer.manager.ScopeBrowserManager.sweep`)."""

    async def sweep(self, *, now: Any = None) -> int: ...


async def computer_browser_sweep_loop(
    manager: _SweepsBrowserTabs, metrics: Metrics, *, interval_seconds: float,
) -> None:
    """Expire idle task-owned tabs and stop idle browsers
    (`ScopeBrowserManager.sweep`), `computer_sweep_loop`'s discipline.
    Started only while `[computer.scoped] enabled`."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            expired = await manager.sweep()
            if expired:
                log.info("expired %d computer browser tabs", expired)
        except Exception:
            log.exception("computer browser sweep failed")
            metrics.inc("nora_errors_total", stage="computer_browser_sweep")


class ComputerLifecycle:
    """The scoped computer's ONE start/close owner ([computer.scoped]).

    `start` spawns the queue loop and the browser tab sweep — once —
    through the caller's `BackgroundTasks`. `__main__` and the eval
    subject share it, so a subject runs the same maintenance the
    deployment does instead of composing a queue nothing ticks.

    Close stops admission and owned loops, drains executors and manager
    events, then releases Redis and the journal before dependent stores.
    Failed cleanup retains resources and can be retried.
    """

    def __init__(self, *, runner: Any = None, manager: Any = None,
                 metrics: Metrics, sweep_seconds: float,
                 tick_seconds: float = 1.0, sessions: Any = None,
                 session_seconds: float = 300.0) -> None:
        self._runner = runner
        self._manager = manager
        self._metrics = metrics
        self._sweep_seconds = sweep_seconds
        self._tick_seconds = tick_seconds
        self._sessions = sessions
        self._session_seconds = session_seconds
        self._spawned = False
        self._closed = False
        self._loops: list[asyncio.Task[None]] = []
        self._close_lock = asyncio.Lock()

    def start(self, background: BackgroundTasks) -> None:
        """Spawn the two maintenance loops, once. A `start` on a closed
        lifecycle spawns nothing — a reopened app composes a new one."""
        if self._spawned or self._closed:
            return
        self._spawned = True
        if self._sessions is not None:
            self._loops.append(background.spawn(computer_sweep_loop(
                self._sessions, self._metrics, interval_seconds=self._session_seconds)))
        if self._runner is not None:
            self._loops.append(background.spawn(computer_queue_loop(
                self._runner, self._metrics, tick_seconds=self._tick_seconds)))
        if self._manager is not None:
            self._loops.append(background.spawn(computer_browser_sweep_loop(
                self._manager, self._metrics,
                interval_seconds=self._sweep_seconds)))

    async def close(self, *, settle_seconds: float = 3.0) -> None:
        """Close only after executors drain; timeout leaves dependencies owned."""
        async with self._close_lock:
            if self._closed:
                return
            if self._runner is not None:
                self._runner.stop_admission()
            for task in self._loops:
                task.cancel()
            await asyncio.gather(*self._loops, return_exceptions=True)
            if self._runner is not None:
                await self._runner.stop(drain_seconds=settle_seconds)
            if self._manager is not None:
                await self._manager.aclose()
            if self._runner is not None:
                await self._runner.close()
            self._closed = True


class _ProbesComputerd(Protocol):
    """The one method `computer_probe_loop` needs
    (`nora.infra.computer.probe.ComputerdProbe`)."""

    async def probe_once(self) -> bool: ...


async def computer_probe_loop(
    probe: _ProbesComputerd, metrics: Metrics, *, interval_seconds: float,
) -> None:
    """Ask computerd whether it answers, on a clock (DEC-261): the first probe
    straight away (the series has no value until it runs) and then every
    `interval_seconds`. The probe reports its own outcome to
    `nora_computer_up` and never raises; this loop is the second barrier, so a
    probe that did raise would be counted `nora_errors_total{stage=
    computer_probe}` and the loop would keep going. Started only while
    `[computer] enabled`."""
    while True:
        try:
            await probe.probe_once()
        except Exception:
            log.exception("computer probe failed")
            metrics.inc("nora_errors_total", stage="computer_probe")
        await asyncio.sleep(interval_seconds)


async def debounce_sweep_loop(
    debounce: tag_module.Debounce,
    metrics: Metrics,
    *,
    interval_seconds: float,
    ttl_seconds: float,
) -> None:
    """Keeps `Debounce._last` bounded: `nora/tag.py` grows one entry per
    distinct (platform, conversation_id) forever otherwise. Same failure
    discipline as `prune_loop` — a failing sweep is logged and counted, never
    fatal to the loop."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            dropped = debounce.sweep(ttl_seconds)
            if dropped:
                log.info("debounce sweep dropped %d stale entries", dropped)
        except Exception:
            log.exception("debounce sweep failed")
            metrics.inc("nora_errors_total", stage="debounce_sweep")


class _ClosesParticipationWindows(Protocol):
    """The two methods `participation_loop` needs from
    `nora.app.participation.ParticipationTrigger` — a narrow local shape like
    `_PrunableStore`, so the loop cannot reach for `note_message` (the
    dispatcher's half of the trigger) or the cursor dicts."""

    async def close_due_windows(self) -> int: ...

    def sweep(self, older_than: float) -> int: ...


async def participation_loop(
    trigger: _ClosesParticipationWindows,
    metrics: Metrics,
    *,
    tick_seconds: float,
    sweep_ttl_seconds: float,
) -> None:
    """The participation occasion's clock.

    its unit of work is one call over every room's window state, so
    `prune_loop`'s single try/except is the right shape here (one fault must
    not take the loop down, and there are no per-scope units to isolate).

    One tick does two things: closes any windows whose room has been quiet
    for `settle_seconds` (running the ONE evaluation each is owed), and
    sweeps rooms idle past `sweep_ttl_seconds` so the trigger's per-room
    cursors stay bounded. It exists only when `[participation] enabled` —
    the composition root's decision, not this loop's.
    """
    log.info("participation loop started: tick every %gs", tick_seconds)
    while True:
        await asyncio.sleep(tick_seconds)
        try:
            evaluated = await trigger.close_due_windows()
            if evaluated:
                log.info("participation: evaluated %d closed window(s)", evaluated)
            dropped = trigger.sweep(sweep_ttl_seconds)
            if dropped:
                log.info("participation sweep dropped %d stale room(s)", dropped)
        except Exception:
            log.exception("participation tick failed")
            metrics.inc("nora_errors_total", stage="participation")


@dataclass(frozen=True)
class SupervisedAdapter:
    """A platform name paired with a zero-arg coroutine factory that runs its
    listen loop.

    Deliberately not typed against a `PlatformAdapter` shape: this module
    has no business knowing any adapter's call signature — the composition root
    binds whatever shape is live at the call site (e.g.
    `functools.partial(adapter.listen, on_message=dispatcher.on_message)`,
    matching `nora.domain.ports.PlatformAdapter.listen`'s keyword-only
    `on_message: Callable[[NormalizedMessage], Awaitable[None]]`). This
    module only needs a zero-arg coroutine factory it can restart.
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
