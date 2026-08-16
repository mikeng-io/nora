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

Five responsibilities, five independent units below, none of which know about
each other:

- `BackgroundTasks` — the strong-reference set `Handler._spawn` used to hold,
  now generic.
- `prune_loop` — moved from `nora.__main__._prune_loop`, unchanged in behaviour.
- `debounce_sweep_loop` — new: keeps `Debounce._last` bounded (see `nora/tag.py`'s
  `sweep`).
- `supervise_adapter` / `start_adapters` — per-adapter restart-with-backoff,
  replacing what would otherwise be a bare `asyncio.gather`.
- `dream_loop` — the Dreamer's clock (Task 11 of
  `docs/superpowers/specs/2026-08-15-room-language-dreamer-design.md`): ticks
  every configured scope, consolidating the room-language slang a
  `RoomConsolidator` distills out of its own message record. A SIBLING of
  `prune_loop`, not a copy of its shape — see `dream_loop`'s own docstring for
  the two differences that are load-bearing rather than stylistic.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .. import tag as tag_module
from ..domain.models import TranscriptRow
from ..domain.ports import RoomConsolidator
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
    """The one method `prune_loop` needs. `RecordStore` (the domain port)
    deliberately has no `prune` — see `nora/domain/ports.py`'s module docstring:
    retention policy is owned by the runtime, not something a stage programs
    against through the port. This narrow local shape mirrors that call-out
    (same pattern as `nora.ingest.stages.audit`'s narrow use of `RecordStore`)."""

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

    Deliberately not typed against a `PlatformAdapter` shape: this module has
    no business knowing any adapter's call signature — the composition root
    binds whatever shape is live at the call site (today,
    `functools.partial(adapter.listen, on_message=dispatcher.on_message)`,
    since `SignalAdapter.listen` now satisfies
    `nora.domain.ports.PlatformAdapter.listen`'s keyword-only
    `on_message: Callable[[NormalizedMessage], Awaitable[None]]` shape). This
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


# ---------------------------------------------------------------------------
# dream_loop — the Dreamer's clock
# ---------------------------------------------------------------------------


class _SinceReadable(Protocol):
    """The one method `dream_loop` needs from a `RecordStore` — a narrow
    local shape mirroring `_PrunableStore` above, for the same reason:
    threading the whole `RecordStore` port through would let this loop reach
    for methods it has no business calling. `RecordStore.since` itself
    already excludes `is_nora` rows and returns ascending order (see that
    port method's own docstring) — this loop relies on both without
    re-deriving either."""

    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]: ...


class _ConsolidationCursor(Protocol):
    """The six `SqlLanguageStore` methods `dream_loop`/`_dream_once` actually
    call — the cursor/measurement surface, never the append/rescore/read one.

    `_SinceReadable`'s own docstring makes this argument for `RecordStore`;
    it applies MORE strongly here, not less. The concrete `SqlLanguageStore`
    also hands out `append_observations`, `rescore` and `room_language` — the
    surface `LanguageConsolidator` owns, not this loop — and an accidental
    call to any of them from here would bypass this whole module's central
    invariant (watermark-last, in the same "cycle" as the consolidator's own
    append) with nothing to catch it. There is no existing named port for
    this surface: `RoomLanguageReader` covers only `room_language`, the ONE
    method this Protocol deliberately excludes. Narrowing to exactly what is
    called is what makes that exclusion a fact a type checker enforces,
    rather than a habit a future edit could quietly break.
    """

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int: ...

    async def watermark_is_behind_the_record(
        self, *, scope_id: str, watermark: int
    ) -> bool: ...

    async def lag(self, *, scope_id: str, after_id: int) -> int: ...

    async def seconds_since_last_message(self, *, scope_id: str) -> float: ...

    async def watermark_age_seconds(
        self, *, consolidator_name: str, scope_id: str
    ) -> float: ...

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None: ...


# The capped-exponential SHAPE `supervise_adapter` uses above, reused rather
# than re-derived — but not its literal numbers. `supervise_adapter` backs off
# a single adapter reconnecting to a LOCAL websocket, where a fast retry is
# cheap; `dream_loop` backs off a call to an EXTERNAL model provider, where a
# 429 answered at full cadence forever makes the outage worse and burns the
# quota (non-negotiable 4). A base near one tick and a much longer cap reflect
# that difference: `supervise_adapter`'s default 60s CAP is this loop's base.
_DREAM_BACKOFF_BASE_SECONDS = 60.0
_DREAM_BACKOFF_MAX_SECONDS = 3_600.0


def _backoff_elapsed(
    scope_id: str,
    failure_count: int,
    last_failure_at: dict[str, float],
    *,
    base_delay_seconds: float = _DREAM_BACKOFF_BASE_SECONDS,
    max_delay_seconds: float = _DREAM_BACKOFF_MAX_SECONDS,
) -> bool:
    """Has enough wall-clock time passed since `scope_id` last failed to try
    it again, at `supervise_adapter`'s own capped-exponential rate?

    `supervise_adapter` expresses this by literally sleeping inside its own
    one-adapter loop — it owns nothing else, so blocking is free. `dream_loop`
    ticks EVERY scope in one loop; sleeping here would stall every scope that
    is not backing off. So this answers the same question `supervise_adapter`
    answers by sleeping — "has the capped-exponential delay for this many
    consecutive failures elapsed" — as a non-blocking check against a
    per-scope last-failure timestamp instead, consulted once per tick rather
    than awaited once per retry. `failure_count` is `dream_loop`'s own
    `failures[scope_id]` (consecutive failures since the last success);
    `last_failure_at` is `dream_loop`'s own local dict, function-scoped to one
    `dream_loop` call and mutated only there, never a module-level singleton.
    """
    delay = min(base_delay_seconds * (2.0 ** (failure_count - 1)), max_delay_seconds)
    return bool((time.monotonic() - last_failure_at.get(scope_id, 0.0)) >= delay)


async def dream_loop(
    record: _SinceReadable, language: _ConsolidationCursor,
    consolidators: Sequence[RoomConsolidator], metrics: Metrics, *,
    scopes: Callable[[], Sequence[str]], tick_seconds: float,
    safety_lag: int, quiet_seconds: float, max_age_seconds: float,
    batch_limit: int,
) -> None:
    """The Dreamer's clock. A sibling of `prune_loop`, with two differences
    that are not stylistic.

    FIRST, the try/except is PER SCOPE. `prune_loop`'s single wrapper is
    correct there because its unit of work is one statement; a dream tick's
    unit is a LIST of scopes, so copying that shape verbatim lets one failing
    scope starve every other scope on that tick.

    SECOND, the backoff comes from `supervise_adapter` rather than from
    `prune_loop`. `prune_all` talks to the local database, where a failure is
    rare and transient; this talks to an external model provider, where a 429
    answered at full cadence forever makes the outage worse and burns the
    quota.

    THIRD — a PLAN DEFECT found while implementing this, in the same spirit
    Task 7's implementer found and fixed one (an impossible cross-layer
    import) rather than silently working around it: the brief's own sketch
    of this loop `await`s `_dream_once` INLINE, one scope after another. A
    single `while True: for scope in scopes(): await _dream_once(...)` is
    ONE coroutine with no concurrency inside it, so `finally: running.discard
    (scope_id)` always runs — and the for-loop's NEXT scope, or the outer
    while-loop's NEXT tick — before a second consideration of that SAME scope
    is even possible. Under that shape `if scope_id in running` can never be
    true: the per-scope lock non-negotiable 1 exists to enforce would be
    unreachable dead code, and a test asserting it (`nora_dream_skipped_total
    {reason="in_flight"}` firing) would stay green whether or not the check
    is even present — CLAUDE.md's own "if reverting the code under test
    doesn't turn the test red, the test is decorative" naming this exact
    failure. It is also the wrong production shape regardless: serializing
    every scope's one model call behind a single await means one slow room
    delays every OTHER room's tick, not just its own.
    So each scope's cycle is dispatched as its own tracked task instead —
    `_run_one` below carries the per-scope try/except/finally UNCHANGED, and
    `running`/`failures`/`last_failure_at` are read and written from
    WHICHEVER scope's task is executing, same as before, just no longer
    forced to interleave through one sequential await chain. This is what
    makes tick N+1 able to find scope "g1" still `running` from tick N — the
    scenario the lock, the backoff and their own tests are named for.
    `_dream_once` itself is untouched: watermark-last, the three fire
    conditions and the partial-batch guard are exactly as given.
    """
    failures: dict[str, int] = {}
    running: set[str] = set()
    # `_backoff_elapsed`'s own per-scope companion state (see its docstring
    # for why it needs a timestamp `failures` alone cannot supply) —
    # function-local to this one `dream_loop` call, exactly like `failures`
    # and `running` beside it.
    last_failure_at: dict[str, float] = {}
    tasks: set[asyncio.Task[None]] = set()

    async def _run_one(scope_id: str) -> None:
        try:
            await _dream_once(
                record=record, language=language, consolidators=consolidators,
                metrics=metrics, scope_id=scope_id, safety_lag=safety_lag,
                quiet_seconds=quiet_seconds, max_age_seconds=max_age_seconds,
                batch_limit=batch_limit,
            )
            failures.pop(scope_id, None)
            last_failure_at.pop(scope_id, None)
        except Exception:
            log.exception("dream cycle failed for %s", scope_id)
            metrics.inc("nora_errors_total", stage="dream")
            failures[scope_id] = failures.get(scope_id, 0) + 1
            last_failure_at[scope_id] = time.monotonic()
        finally:
            running.discard(scope_id)

    try:
        while True:
            await asyncio.sleep(tick_seconds)
            for scope_id in scopes():
                if scope_id in running:
                    # A slow extraction is still going. Skipping is the whole
                    # guarantee: re-entering would read the same un-advanced
                    # watermark and append the same evidence twice, and the
                    # rendered «12x, 3 people» would be a fabricated number.
                    metrics.inc("nora_dream_skipped_total", reason="in_flight")
                    continue
                if failures.get(scope_id) and not _backoff_elapsed(
                    scope_id, failures[scope_id], last_failure_at,
                ):
                    continue
                running.add(scope_id)
                task = asyncio.create_task(_run_one(scope_id))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
    finally:
        # `BackgroundTasks.cancel_all`'s own shape, not just its `.cancel()`
        # half: that method's docstring records the exact gap a cancel-only
        # cleanup leaves — a done-callback (`tasks.discard` here) is
        # delivered via `call_soon`, one loop tick later, so `task.cancel()`
        # alone does not guarantee the task has actually finished unwinding
        # by the time this function returns. Cancel every pending task FIRST,
        # THEN await each — awaiting inside a `finally` while THIS coroutine
        # is itself mid-cancellation is safe (the awaits below run to
        # completion before the outer `CancelledError` continues propagating,
        # provided this task is not cancelled a second time meanwhile).
        # `list(tasks)` snapshots before iterating: `add_done_callback` above
        # mutates `tasks` via `discard` as each one finishes, and iterating a
        # set while it is being mutated raises.
        pending = list(tasks)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _dream_once(
    *, record: _SinceReadable, language: _ConsolidationCursor,
    consolidators: Sequence[RoomConsolidator], metrics: Metrics, scope_id: str,
    safety_lag: int, quiet_seconds: float, max_age_seconds: float, batch_limit: int,
) -> None:
    watermark = await language.watermark_for(
        consolidator_name="language", scope_id=scope_id)

    # The fail-closed tell. A watermark below the oldest surviving row means
    # messages were deleted before they were ever read — R-27's `unavailable`
    # («I could not see those»), never `nothing_found` («nothing happened»).
    # Structurally zero while `store.prune_enabled` is false; this is what
    # notices if anyone turns the sweep back on.
    if await language.watermark_is_behind_the_record(
            scope_id=scope_id, watermark=watermark):
        metrics.inc("nora_dream_evidence_lost_total", scope=scope_id)

    lag = await language.lag(scope_id=scope_id, after_id=watermark)
    quiet = await language.seconds_since_last_message(scope_id=scope_id)
    age = await language.watermark_age_seconds(
        consolidator_name="language", scope_id=scope_id)

    fires = (
        lag > safety_lag
        or (lag > 0 and quiet >= quiet_seconds)
        or age >= max_age_seconds      # fires at lag == 0 ON PURPOSE
    )
    if not fires:
        return

    batch = await record.since(
        conversation_id=scope_id, after_id=watermark, limit=batch_limit)

    for consolidator in consolidators:
        # An empty batch still reaches the consolidator, which still rescores.
        # Decay is TIME-driven: two of the three fire conditions need lag > 0,
        # so without this a dormant room never fades a word and its lexicon
        # stays `live` describing a slang era that ended months ago.
        await consolidator.consolidate(scope_id=scope_id, batch=batch)

    if batch:
        # LAST, and after the appends have committed. The unsafe direction is
        # watermark-first: a crash between the two loses that range forever,
        # with nothing able to notice — `nora_dream_evidence_lost_total` above
        # measures the PRUNING case and is structurally blind to this one.
        # Append-first plus the natural-key constraint makes a retry a no-op
        # instead, so over-reading is the safe failure and under-reading is not.
        await language.advance_watermark(
            consolidator_name="language", scope_id=scope_id,
            last_message_id=batch[-1].id,
        )
    else:
        # PLAN DEFECT found and fixed: without this, the 24h/age-only firing
        # (the ONE condition that fires at `lag == 0`) is a LATCH with no
        # reset. `watermark_age_seconds` reads `updated_at`, and — before this
        # branch existed — `updated_at` was written ONLY by the `if batch:`
        # arm above, which an empty batch never reaches. So a scope that has
        # never been consolidated stays at `age = inf` forever (fires every
        # tick, and can never write a row to stop it), and a scope that WAS
        # consolidated once and then went dormant fires once at 24h and then
        # NEVER STOPS FIRING — `updated_at` stays pinned at its old value,
        # `age` never drops back under `max_age_seconds`, and a background job
        # meant to run once a day instead reruns a full `rescore` on every
        # tick forever (288x/day at the shipped 300s cadence), unattended.
        # Refresh the row at the SAME `last_message_id` (the watermark did not
        # move — nothing was appended, so there is nothing to lose) purely to
        # reset the clock `watermark_age_seconds` reads. `advance_watermark`'s
        # own natural key makes this an idempotent no-op on the CURSOR value;
        # only `updated_at` changes.
        await language.advance_watermark(
            consolidator_name="language", scope_id=scope_id,
            last_message_id=watermark,
        )
