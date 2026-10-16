"""Lifetime concerns of the running process: background tasks, retention and
sweep loops, adapter supervision and the Dreamer's clock.

This module owns background-task lifetime: the strong-reference task set every
fire-and-forget spawn is tracked in, the retention prune sweep, the `Debounce`
TTL sweep, and per-adapter restart-with-backoff supervision.
`nora.app.composition.build_dependencies` constructs them for production
traffic. It takes **dependencies, not config**: the composition root passes a
store, metrics, adapters and intervals, never `Config` itself (`nora.app` is one
of only two packages allowed to read it — see `nora/app/composition.py` — and
`runtime.py` deliberately isn't one of them).

Independent units below, none of which know about each other:

- `BackgroundTasks` — the strong-reference set every fire-and-forget task is
  held in.
- `prune_loop` — the retention sweep.
- `debounce_sweep_loop` — keeps `Debounce._last` bounded (see `nora/tag.py`'s
  `sweep`).
- `supervise_adapter` / `start_adapters` — per-adapter restart-with-backoff,
  instead of a bare `asyncio.gather`.
- `dream_loop` — the Dreamer's clock: ticks every configured scope and
  unit, running each `DreamAdaptor` whose fire condition is met. A SIBLING
  of `prune_loop`, not a copy of its shape — see `dream_loop`'s own docstring
  for the two differences that are load-bearing rather than stylistic.

  It is general: what it ticks is whatever `composition.py` puts in
  `adaptors`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .. import tag as tag_module
from ..agent.dreamer import AdaptorSchedule
from ..domain.media_join import MediaJoin
from ..domain.models import TranscriptRow
from ..domain.ports import DreamAdaptor
from ..obs.metrics import Metrics
from ..obs.tracing import TRACE_KIND, annotate, attached, span, traced_read

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

    `embed` (`[memory_index] embed`) is the
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

    A sibling of `prune_loop`/`debounce_sweep_loop`, not of `dream_loop`:
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
    """The six `SqlLanguageStore` methods `dream_loop` actually
    calls — the cursor/measurement surface, never the append/rescore/read one.

    Like `_SinceReadable` for `RecordStore`, naming exactly what is called makes
    the exclusion a fact a type checker enforces rather than a habit a future
    edit could quietly break: an accidental call to anything else on the store
    would bypass this module's central invariant (watermark-last, in the same
    cycle as the adaptor's own write) with nothing to catch it.
    """

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int: ...

    async def watermark_is_behind_the_record(
        self, *, scope_id: str, watermark: int
    ) -> bool: ...

    async def lag(self, *, scope_id: str, after_id: int) -> int: ...

    async def seconds_since_last_message(self, *, scope_id: str) -> float: ...

    async def head_message_id(self, *, scope_id: str) -> int: ...

    async def watermark_age_seconds(
        self, *, consolidator_name: str, scope_id: str
    ) -> float: ...

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None: ...


class _DreamOutcome(Protocol):
    """What one adaptor run reports, seen from the clock: the R-27 status and
    nothing else.

    A read-only PROPERTY rather than a bare attribute so the concrete
    `DreamOutcome`'s `Literal["ok", "nothing_found", "unavailable"]` satisfies
    it — a plain `status: str` on a Protocol is mutable and therefore
    invariant, which a narrower literal type does not match.

    The clock acts on exactly one value (`"unavailable"`, which drives the
    backoff and holds the watermark), so narrowing to one property is what
    keeps `nora.app` from naming a type that lives in `nora.agent` — which it
    may not import, because that package imports `strands`.
    """

    @property
    def status(self) -> str: ...


class _KeyEvidence(Protocol):
    """An adaptor's own evidence, for a key that is not a room: a Character
    session for a person, the unit or Nora counts what is new for ITS key since the key's
    watermark, and reads the newest of it when it wakes.

    `newest` returns the items handed to the session (newest `limit`, oldest
    first) and the CURSOR to advance the watermark to on success: the newest
    position among everything new, read or not, so a backlog past `limit` is
    consumed rather than walked (a single evolving account wants what was said
    lately, `AdaptorSchedule.seed_at_head`'s reason). The cursor is whatever
    integer the kind counts by (a Record id, a revision count): the loop only
    stores it and hands it back as `after`.
    """

    async def count(self, *, key: str, after: int) -> int: ...

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[Sequence[Any], int]: ...


class _ScheduleClock(Protocol):
    """Where a watermark-free scheduled adaptor's last fire is remembered —
    `nora.infra.sql.dream_schedule.SqlDreamScheduleClock`, seen from the loop.

    A narrow local shape rather than a `nora.domain.ports` Protocol, the same
    reason `_SinceReadable` and `_ConsolidationCursor` beside it are: the loop
    needs two verbs and must not be able to reach a third.

    **`last_fired` returning `None` is part of the contract, not an edge
    case.** A never-fired pair must not be reported as one that fired at epoch
    0: both fire exactly once on the next tick, so a store that substituted 0
    would pass every test while making a brand-new adaptor's first run look
    like a decades-old one to anybody reading the row.
    """

    async def last_fired(
        self, *, adaptor_name: str, key: str
    ) -> int | None: ...

    async def record_fire(
        self, *, adaptor_name: str, key: str, at_ms: int
    ) -> None: ...


def _now_ms() -> int:
    """Wall clock, epoch milliseconds — the unit every bigint timestamp in
    this schema carries, and the one `_slot` needs (see
    `_dream_adaptor_once` for why `monotonic()` cannot serve here)."""
    return int(time.time() * 1000)


def _slot(at_ms: int, sched: AdaptorSchedule) -> int:
    """Which scheduling slot an instant falls in.

    `floor((t - offset) / interval)`. Two instants in the same slot are the
    same scheduled occasion, so a schedule fires at most once per slot — which
    is what makes "daily at 06:00 UTC" expressible and what lets two adaptors
    sharing an interval be given different PHASES.

    **Arithmetic in MILLISECONDS, not seconds.** Flooring the instant to whole
    seconds first would round every sub-second interval to zero, and the
    suite's schedule tests run at sub-second intervals so a real clock can
    exercise a real interval quickly.

    Integer floor division, and Python floors toward negative infinity for
    negative operands, which is the behaviour wanted: an offset larger than the
    instant (a schedule configured with a phase later in the day than the epoch
    instant under test) must still land in a well-defined slot rather than
    truncating toward zero and colliding with its neighbour.
    """
    interval_ms = int(sched.min_interval_seconds * 1000)
    if interval_ms <= 0:
        # A non-positive interval means "no floor" — every instant is its own
        # slot. Returning the instant itself makes that true by construction
        # rather than by a division that would raise.
        return at_ms
    return (at_ms - int(sched.phase_offset_seconds * 1000)) // interval_ms


class _AdaptorRunner(Protocol):
    """`nora.agent.dreamer.DreamerRuntime`, seen from the clock: run one
    adaptor for one key and say what happened.

    A narrow local shape rather than a `nora.domain.ports` Protocol, the same
    reason `_SinceReadable`, `_ConsolidationCursor` and `_InstructionVersions`
    beside it are: threading the whole runtime through would let this loop
    reach for its budget table or its tool source, neither of which is any of
    the clock's business. **`nora.app` may not import `strands`, and the
    concrete runtime lives in `nora.agent` because it must** — so the return is
    read structurally, by the one attribute this loop acts on.
    """

    async def run(
        self, *, adaptor: DreamAdaptor, key: str, batch: Sequence[TranscriptRow]
    ) -> _DreamOutcome: ...


# The capped-exponential SHAPE `supervise_adapter` uses above, reused rather
# than re-derived — but not its literal numbers. `supervise_adapter` backs off
# a single adapter reconnecting to a LOCAL websocket, where a fast retry is
# cheap; `dream_loop` backs off a call to an EXTERNAL model provider, where a
# 429 answered at full cadence forever makes the outage worse and burns the
# quota. A base near one tick and a much longer cap reflect that difference:
# `supervise_adapter`'s default 60s CAP is this loop's base.
_DREAM_BACKOFF_BASE_SECONDS = 60.0
_DREAM_BACKOFF_MAX_SECONDS = 3_600.0


#: How far a repeatedly-failing unit's batch is allowed to shrink. A floor
#: rather than 1 because a one-message batch is not a cheaper version of the
#: same work — an objective that reads a CONVERSATION asks a different question
#: of a single message in isolation, with a worse answer.
_DREAM_MIN_SHRUNK_BATCH = 5


def _shrunk_batch_limit(base: int, failure_count: int) -> int:
    """How many rows this cycle may read, given `failure_count` consecutive
    failures on this same `(adaptor, key)`.

    **Why a retry reads LESS rather than the same thing again.** `unavailable`
    holds the watermark on purpose — nobody read that batch, so advancing would
    consume it with nothing left to retry. The cost of that guarantee is that
    the next fire re-reads the IDENTICAL range, and for a failure caused BY the
    range (an output that will not fit the model's per-call ceiling) the retry
    is guaranteed to fail the same way. Only `_DREAM_BACKOFF_MAX_SECONDS` would
    then slow it, to a cadence that looks like a schedule rather than a fault.

    Halving per consecutive failure turns that into a search (80 -> 40 -> 20):
    a batch whose output overflowed by a few percent fits on the FIRST retry,
    so the unit heals itself inside two fires.

    **Why this is safe for the failure it is NOT for.** A provider outage or a
    429 also lands as `unavailable`, and shrinking that batch fixes nothing —
    but it costs nothing either: the smaller batch succeeds when the provider
    returns, the watermark advances, `failures` clears, and the very next fire
    is back at full size. Shrinking is a cheap wrong answer for a transient
    failure and the only right answer for a poisoned one, so it is applied to
    both rather than guessed between.

    `failure_count` is `dream_loop`'s own consecutive-failure count, cleared on
    any non-`unavailable` outcome. Consecutive failures therefore imply an
    UNMOVED watermark, which is what makes this a statement about the same
    RANGE rather than about the adaptor's general health.

    Never returns more than `base`: an adaptor whose configured batch is
    already below the floor is not ENLARGED by failing.
    """
    if failure_count <= 0:
        return base
    shrunk = base >> min(failure_count, 16)
    return min(base, max(_DREAM_MIN_SHRUNK_BATCH, shrunk))


def _backoff_elapsed(
    key: str,
    failure_count: int,
    last_failure_at: dict[str, float],
    *,
    base_delay_seconds: float = _DREAM_BACKOFF_BASE_SECONDS,
    max_delay_seconds: float = _DREAM_BACKOFF_MAX_SECONDS,
) -> bool:
    """Has enough wall-clock time passed since `key` last failed to try it
    again, at `supervise_adapter`'s own capped-exponential rate?

    `supervise_adapter` expresses this by literally sleeping inside its own
    one-adapter loop — it owns nothing else, so blocking is free. `dream_loop`
    ticks EVERY unit of work in one loop; sleeping here would stall every unit
    that is not backing off. So this answers the same question
    `supervise_adapter` answers by sleeping — "has the capped-exponential
    delay for this many consecutive failures elapsed" — as a non-blocking
    check against a per-unit last-failure timestamp instead, consulted once
    per tick rather than awaited once per retry. `failure_count` is
    `dream_loop`'s own `failures[key]` (consecutive failures since the last
    success); `last_failure_at` is `dream_loop`'s own local dict,
    function-scoped to one `dream_loop` call and mutated only there, never a
    module-level singleton.

    **`key` is the ISOLATION BOUNDARY, and it is not always a `scope_id`
    (DEC-133).** The consolidator path keys on the scope; an adaptor keys on
    `(adaptor, its own key)`. Isolating failures per adaptor while leaving the
    backoff keyed per scope would let one failing adaptor back off every OTHER
    adaptor in that scope — the exact bug per-adaptor isolation exists to
    prevent, reintroduced one layer down. The key moves WITH the boundary or the
    isolation is a lie.
    """
    delay = min(base_delay_seconds * (2.0 ** (failure_count - 1)), max_delay_seconds)
    return bool((time.monotonic() - last_failure_at.get(key, 0.0)) >= delay)


def _log_instruction_versions(instructions: _InstructionVersions | None) -> None:
    """One line per tick naming the instruction revision this tick is running,
    the way `nora.engagement.pipeline` logs
    `doctrine_versions={…}` at the start of a tag.

    Why the whole thing is wrapped: this is a LOG LINE, and the corpus it reads
    is hot-reloading off a bind mount. A directory that vanished mid-run, or a
    permission change, must not be able to take the Dreamer's clock down —
    which is what an uncaught exception here would do, because `dream_loop`'s
    try/except is per SCOPE and this runs outside it. Reported at WARNING, so
    the failure is visible rather than swallowed.
    """
    if instructions is None:
        return
    try:
        log.info("dream tick: instruction_versions=%s", instructions.versions())
    except Exception as exc:                      # noqa: BLE001 - see docstring
        log.warning("dream tick: instruction versions unavailable: %s", exc)


class _InstructionVersions(Protocol):
    """The Dreamer instruction corpus, seen from the clock: one mapping of
    artifact name to version, and nothing else.

    A documented structural type rather than a `nora.domain.ports` Protocol,
    the same reason the observers' own `_ObjectiveSource` is one: a shape with
    one consumer does not earn a shared contract suite. `nora.app` COULD import
    `nora.instructions.dreamer` directly and be within the layer contract —
    this narrows the loop's dependency to the one method it uses, so a test can
    supply a mapping without building a corpus on disk.
    """

    def versions(self) -> dict[str, int]: ...


async def seed_watermarks(
    language: _ConsolidationCursor,
    adaptors: Sequence[DreamAdaptor],
    *,
    keys: Callable[[DreamAdaptor], Sequence[str]],
    schedules: Mapping[str, AdaptorSchedule] | None = None,
    source: str = "language",
) -> int:
    """Give every watermarked adaptor that has none a cursor, BEFORE the new
    schedule ever fires. Returns the number of rows seeded.

    **The hazard this exists for is cheap to forget and expensive to
    discover.** `watermark_for` returns 0 for an unknown `(name, scope)` pair.
    So an adaptor with a NEW name — a migrated observer, or a tier that never
    ran — starts at 0 and re-reads the room from the beginning, `batch_limit`
    rows at a time, paying a model call per batch to re-derive evidence that
    already exists. It is SAFE (the natural key makes the append idempotent) and
    it is slow, expensive, and looks exactly like a bug.

    Seeded from another adaptor's row rather than from the record head, and one
    mechanism covers both cases it has to: a RENAMED adaptor inherits the
    cursor of the thing it replaced, and a genuinely NEW one starts roughly
    where the room consolidator has already read to — «from now» — rather than
    at the dawn of the record.

    **Conditional on the row being absent, and that is load-bearing.**
    `advance_watermark` is an UPSERT: calling it unconditionally would overwrite
    a live cursor, which is the disaster this function exists to avoid, not a
    stronger version of the fix. A pair already at 0 with a source also at 0 is
    left alone — there is nothing to inherit and nothing to skip.

    Idempotent: run it every startup. The second run finds every row present
    and seeds nothing.
    """
    seeded = 0
    for adaptor in adaptors:
        if not adaptor.uses_watermark or adaptor.name == source:
            continue
        if getattr(adaptor, "evidence", None) is not None:
            # Its cursor counts its OWN evidence, never a room's messages, so
            # nothing can be inherited, and its first new item wakes it.
            continue
        for key in keys(adaptor):
            if await language.watermark_for(
                consolidator_name=adaptor.name, scope_id=key
            ):
                continue
            inherited = await language.watermark_for(
                consolidator_name=source, scope_id=key
            )
            origin = source
            if not inherited:
                # NOTHING TO INHERIT — the source cursor never covered this
                # scope. At 0 the adaptor would re-read the room from the
                # beginning, `batch_limit` rows at a time. For an
                # append-idempotent extractor that is merely slow; for an
                # adaptor whose output is a single evolving HEAD it is worse
                # than slow, because every one of those catch-up runs installs
                # an answer derived from the room's PAST as the current one.
                sched = (schedules or {}).get(adaptor.name)
                if sched is None or not sched.seed_at_head:
                    continue
                inherited = await language.head_message_id(scope_id=key)
                origin = "the record head"
                if not inherited:
                    # A scope with no messages at all. Nothing to seed past,
                    # and 0 is already the right cursor for it.
                    continue
            await language.advance_watermark(
                consolidator_name=adaptor.name, scope_id=key,
                last_message_id=inherited,
            )
            seeded += 1
            log.info(
                "seeded dream watermark: adaptor=%s scope=%s from %s at %d",
                adaptor.name, key, origin, inherited,
            )
    return seeded


async def _dream_adaptor_once(
    *, adaptor: DreamAdaptor, runtime: _AdaptorRunner,
    record: _SinceReadable, language: _ConsolidationCursor, metrics: Metrics,
    key: str, safety_lag: int, quiet_seconds: float, max_age_seconds: float,
    batch_limit: int,
    media_join: MediaJoin | None = None,
    schedules: Mapping[str, AdaptorSchedule] | None = None,
    clock: _ScheduleClock | None = None,
    now_ms: int | None = None,
    failure_count: int = 0,
) -> str | None:
    """One adaptor, one key, one cycle.

    An adaptor with `uses_watermark = False` gets NO watermark, lag, quiet or
    age condition and runs on every tick with an empty batch. That is the
    right shape whenever an adaptor's real input is a TABLE rather than a
    message batch — a raw-message proxy for it would fire on the wrong
    signal. The ONE exception is a watermark-free adaptor with an
    `AdaptorSchedule` entry: it fires on a daily SLOT rather than on backlog,
    because there is no watermark to age and no lag to read.

    **The slot, and why it is not elapsed time.** A schedule fires when the
    current slot is one nobody has fired in yet, not when
    `now - last_fired >= min_interval_seconds` against an in-process monotonic
    clock, for two reasons:

    - **An in-process clock resets on every restart.** A recreate would begin
      with no recorded fire and the next tick would always fire, so "daily"
      would mean "once per deploy". `_ScheduleClock` keeps it in
      `dream_schedule_state`, where it survives the process.
    - **Elapsed time cannot express a phase offset.** The fact tier's three
      adaptors have a real data dependency (candidates write, adjudication
      reads them, entity resolution reads what adjudication wrote), and
      `dream_loop` dispatches adaptors as CONCURRENT tasks with no ordering.
      Separating their PERIODS does nothing; separating their PHASES does. A
      slot — `floor((t - offset) / interval)` — makes "daily at 06:00 UTC"
      expressible.

    Wall clock, therefore, not `monotonic()`: a slot is arithmetic over an
    absolute instant. The cost is that a backwards clock jump can re-fire once;
    that is the right side of the trade, because the alternative is
    reset-every-deploy, which happens daily rather than never.

    A caller supplying no `clock` gets every-tick — the by-omission default
    `AdaptorSchedule` itself documents.

    **Returns the run's STATUS, or `None` when the fire conditions did not
    fire.** The caller needs it: `DreamerRuntime.run` absorbs every failure
    into an `unavailable` outcome and RETURNS NORMALLY, so a provider outage
    reaches `_run_adaptor` as a successful call. Without this return value the
    exponential backoff could never engage on the one failure it exists for, and
    the success path would clear backoff accrued from an unrelated failure.
    """
    sched = (schedules or {}).get(adaptor.name)
    evidence: _KeyEvidence | None = getattr(adaptor, "evidence", None)
    if evidence is not None:
        return await _dream_evidence_once(
            adaptor=adaptor, runtime=runtime, language=language, metrics=metrics,
            evidence=evidence, key=key, sched=sched, batch_limit=batch_limit,
            max_age_seconds=max_age_seconds,
        )
    if not adaptor.uses_watermark:
        if sched is None:
            return (await runtime.run(adaptor=adaptor, key=key, batch=())).status
        if clock is None:
            # LOUD, like the runtime-less branch in `dream_loop` and for the
            # same reason: a wiring mistake whose only symptom is silence.
            # "No entry in the mapping means every tick" is the safe default
            # for an adaptor nobody scheduled; falling back to it for one that
            # WAS scheduled is not. `fact-adjudication` on every tick is a
            # 20-turn research loop every 5 minutes per unit — 288 a day
            # — against a background clock nobody is watching.
            log.error(
                "dream adaptor %s has a schedule but no clock to read; it "
                "will run EVERY TICK instead of on its schedule", adaptor.name,
            )
            return (await runtime.run(adaptor=adaptor, key=key, batch=())).status
        # A watermark-free adaptor on its own clock fires on its SLOT, never on
        # backlog: there is no cursor to age, and requiring `lag > 0` would
        # make a silent room's schedule never fire at all — which is precisely
        # what a time-driven pass (one that retires notes on anchor dates,
        # whether or not anyone speaks) must not do.
        now = _now_ms() if now_ms is None else now_ms
        # The read goes through `traced_read` like the watermark reads below
        # (this method IS the adaptor's cursor — there is no watermark here).
        # `record_fire` two lines down is a WRITE and stays unwrapped, same as
        # `advance_watermark` in the branch below.
        fired_at = await traced_read(
            "dream_schedule", "last_fired",
            clock.last_fired(adaptor_name=adaptor.name, key=key),
        )
        # An ABSENT row fires; an epoch-0 row does not get invented for it.
        # `last_fired` returns None for a pair that has never run, and the
        # first slot it sees is by definition one it has not fired in.
        if fired_at is not None and _slot(now, sched) == _slot(fired_at, sched):
            # COUNTED, not silently returned: `nora_dream_run_total` counts
            # runs and not their reason, so a schedule that never fires and a
            # tier with nothing to do would be the same series. A hold is the
            # reading that separates them.
            metrics.inc("nora_dream_skipped_total", reason="schedule")
            return None
        status = (await runtime.run(adaptor=adaptor, key=key, batch=())).status
        # Stamped AFTER the run, and NEVER on `unavailable`.
        #
        # Stamping first would not prevent a concurrent second copy:
        # `dream_loop` already holds a per-`(adaptor, key)` `running` set,
        # checked before dispatch and released in a `finally`, so the second
        # copy cannot happen in-process — and across a restart the old process
        # is gone, where re-running is the behaviour wanted.
        #
        # Pre-stamping would cost real behaviour. `unavailable` is what
        # `DreamerRuntime.run` returns for a 429 that outlived the retry
        # policy, a timeout, or a killed loop; claiming the slot before the run
        # would burn the whole day on it, with no retry, because a schedule hold
        # returns `None` and `None` is not `unavailable` — so `dream_loop`'s
        # exponential backoff would clear on the very next tick and never reach
        # its second step for a scheduled adaptor. It would also write
        # `last_fired_at` and `updated_at` for a run that did nothing, which is
        # R-27's forbidden substitution landing in the one table an operator
        # reads to ask whether this clock is alive.
        #
        # Leaving `unavailable` unstamped keeps the slot open, so the next tick
        # retries inside the same day and the backoff governs the pace.
        if status != "unavailable":
            await clock.record_fire(adaptor_name=adaptor.name, key=key, at_ms=now)
        return status

    # The Dreamer's cursor reads are traced. `traced_read`
    # (`nora.obs.tracing`) is the ONE wrapper — no store class under
    # `nora/infra/**` has a span of its own — named `store.<store>.<method>`
    # per call, nested under whatever `span` is current (`dream_loop`'s
    # per-unit `dream:<name>` root, or a root of its own for a standalone
    # caller with no loop around it).
    watermark = await traced_read(
        "language", "watermark_for", language.watermark_for(
            consolidator_name=adaptor.name, scope_id=key),
    )

    # The fail-closed tell: a watermark below the
    # oldest surviving row means messages were deleted before they were ever
    # read — R-27's `unavailable`, never `nothing_found`.
    if await traced_read(
        "language", "watermark_is_behind_the_record",
        language.watermark_is_behind_the_record(scope_id=key, watermark=watermark),
    ):
        metrics.inc("nora_dream_evidence_lost_total", scope=key)

    lag = await traced_read(
        "language", "lag", language.lag(scope_id=key, after_id=watermark),
    )
    quiet = await traced_read(
        "language", "seconds_since_last_message",
        language.seconds_since_last_message(scope_id=key),
    )
    age = await traced_read(
        "language", "watermark_age_seconds", language.watermark_age_seconds(
            consolidator_name=adaptor.name, scope_id=key),
    )

    fires: bool
    if sched is not None:
        # The adaptor's own clock: backlog present AND the watermark row at
        # least `min_interval_seconds` old — a daily fact-extraction batch, not
        # the shared lag/quiet/age shape.
        # The `else` latch-reset below stays mandatory here too:
        # `watermark_age_seconds` reads `updated_at`, and only the batch arm
        # writes it, so an interval-only fire would otherwise latch on forever.
        stale = lag > 0 and age >= sched.min_interval_seconds
        # The evidence wake: enough
        fires = stale or (sched.wake_batch is not None and lag >= sched.wake_batch)
    else:
        fires = (
            lag > safety_lag
            or (lag > 0 and quiet >= quiet_seconds)
            or age >= max_age_seconds      # fires at lag == 0 ON PURPOSE
        )
    if not fires:
        return None

    base_limit = sched.batch_limit if sched is not None else batch_limit
    limit = _shrunk_batch_limit(base_limit, failure_count)
    if limit != base_limit:
        # COUNTED, for `nora_dream_skipped_total{reason="schedule"}`'s reason:
        # a unit quietly healing itself and a unit that never had a problem
        # look identical in `nora_dream_run_total`. This series says
        # «something about THIS range does not fit».
        metrics.inc("nora_dream_batch_shrunk_total", adaptor=adaptor.name)
        log.warning(
            "dream batch shrunk after %d consecutive failures: adaptor=%s "
            "scope=%s limit=%d (from %d)",
            failure_count, adaptor.name, key, limit, base_limit,
        )
    batch = await traced_read(
        "record", "since", record.since(
            conversation_id=key, after_id=watermark, limit=limit),
    )
    if media_join is not None:
        # HERE rather than inside each adaptor: every scope-keyed dream
        # adaptor reads this one batch, so joining once covers all of them and
        # none of them has to hold a media store. The culture adaptor renders
        # these rows for a model — a photo it cannot see is exactly the
        # content that would be lost.
        batch = list(await traced_read(
            "media_join", "enrich", media_join.enrich(batch, path="dreamer"),
        ))

    outcome = await runtime.run(adaptor=adaptor, key=key, batch=batch)

    if outcome.status == "unavailable":
        # NOBODY READ THIS BATCH. Hold the cursor — this is
        # `ExtractionUnparseableError`'s guarantee, now a value: advancing here
        # consumes those messages permanently with nothing left to retry, and
        # `nora_dream_evidence_lost_total` above is structurally blind to it
        # (it measures PRUNING). An ERROR is never a PASS.
        return outcome.status

    if batch:
        # LAST, and after the write has committed. The unsafe direction is
        # watermark-first: a crash between the two loses that range forever.
        await language.advance_watermark(
            consolidator_name=adaptor.name, scope_id=key,
            last_message_id=batch[-1].id,
        )
    else:
        # The age-only condition is the ONE that fires at `lag == 0`, and
        # without this it is a latch with no reset: `watermark_age_seconds`
        # reads `updated_at`, which only the `if batch:` arm writes, so a
        # dormant scope fires once at `max_age_seconds` and then NEVER STOPS
        # FIRING — 288x/day at the shipped 300s cadence, unattended. Refresh
        # the row at the SAME cursor value purely to reset that clock; the
        # upsert makes it a no-op on `last_message_id`.
        await language.advance_watermark(
            consolidator_name=adaptor.name, scope_id=key,
            last_message_id=watermark,
        )
    return outcome.status


async def _dream_evidence_once(
    *, adaptor: DreamAdaptor, runtime: _AdaptorRunner,
    language: _ConsolidationCursor, metrics: Metrics, evidence: _KeyEvidence,
    key: str, sched: AdaptorSchedule | None, batch_limit: int,
    max_age_seconds: float,
) -> str | None:
    """One cycle of an adaptor whose key is not a room: its OWN evidence count
    decides the wake, on the rule rooms use. It fires when the new evidence reaches the schedule's
    `wake_batch`, or when the key's watermark is `min_interval_seconds` old
    (stale) with ANY new evidence. A key with nothing new never fires, and a
    key that never ran has an infinitely old watermark, so its first new item
    wakes it.

    The cursor is the same `consolidation_watermark` row every adaptor keeps,
    and the order is the same: run, then advance only on a run that was not
    `unavailable` («the watermark advances only on success»).
    """
    watermark = await traced_read(
        "language", "watermark_for", language.watermark_for(
            consolidator_name=adaptor.name, scope_id=key),
    )
    new = await traced_read(
        "character", "count", evidence.count(key=key, after=watermark),
    )
    if new <= 0:
        return None
    age = await traced_read(
        "language", "watermark_age_seconds", language.watermark_age_seconds(
            consolidator_name=adaptor.name, scope_id=key),
    )
    stale_after = sched.min_interval_seconds if sched is not None else max_age_seconds
    batch = sched.wake_batch if sched is not None else None
    if not (age >= stale_after or (batch is not None and new >= batch)):
        return None
    # NOT shrunk on a failure, unlike a room's batch: a Character session reads
    # the NEWEST `limit` items, so a smaller limit changes which items it is
    # handed on every retry, and its output is one account, not a verdict per
    # row, so a smaller read does not make it fit. The retry bound is the
    # session's own (`EntitySessions._reject`), and it names the batch by this
    # key's held watermark, which only a success moves.
    limit = sched.batch_limit if sched is not None else batch_limit
    items, cursor = await traced_read(
        "character", "newest", evidence.newest(key=key, after=watermark, limit=limit),
    )
    outcome = await runtime.run(adaptor=adaptor, key=key, batch=items)
    if outcome.status == "unavailable":
        return outcome.status
    await language.advance_watermark(
        consolidator_name=adaptor.name, scope_id=key,
        last_message_id=max(cursor, watermark),
    )
    return outcome.status


async def dream_loop(
    record: _SinceReadable, language: _ConsolidationCursor,
    metrics: Metrics, *,
    scopes: Callable[[], Sequence[str]], tick_seconds: float,
    safety_lag: int, quiet_seconds: float, max_age_seconds: float,
    batch_limit: int,
    media_join: MediaJoin | None = None,
    instructions: _InstructionVersions | None = None,
    adaptors: Sequence[DreamAdaptor] = (),
    runtime: _AdaptorRunner | None = None,
    units: Callable[[], Sequence[str]] = lambda: (),
    schedules: Mapping[str, AdaptorSchedule] | None = None,
    schedule_clock: _ScheduleClock | None = None,
    # `[dreamer] max_unit_hold_seconds`: the ONE wall clock `_run_adaptor`
    # wraps its whole call to `_dream_adaptor_once` in. The store phases have
    # their own bounds; everything else inside it — `clock.last_fired`/
    # `record_fire`, every `language.*` cursor read, `record.since`,
    # `media_join.enrich` — is a bare `await`, and any one of them hanging would
    # leave this loop holding `<adaptor>:<key>` in `running` forever.
    #
    # **TEST-ONLY DEFAULT — this number is NOT what production runs.**
    # `nora.app` may not read `Config` (this module's own docstring, top of
    # file), so `dream_loop` cannot resolve `[dreamer]
    # max_unit_hold_seconds` itself; `nora/__main__.py` is the one caller
    # that matters in production and it ALWAYS passes
    # `Config.dreamer_max_unit_hold_seconds()` explicitly — the resolver
    # that derives a real number from the deployment's own budgets. This
    # default exists ONLY so the tests that construct a `dream_loop` with no
    # opinion on the wall clock are not effectively unbounded by omission; its
    # one requirement is "comfortably larger than any test's own tick interval
    # or sleep window".
    max_unit_hold_seconds: float = 1200.0,
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

    THIRD — each scope's cycle is dispatched as its own tracked task, not
    `await`ed inline. A single `while True: for scope in scopes(): await ...` is
    ONE coroutine with no concurrency inside it, so `finally: running.discard
    (scope_id)` would always run before a second consideration of that SAME
    scope was even possible: `if scope_id in running` could never be true, the
    per-scope lock non-negotiable 1 exists to enforce would be unreachable dead
    code, and a test asserting it (`nora_dream_skipped_total
    {reason="in_flight"}` firing) would stay green whether or not the check is
    present. Inline awaiting would also serialize every scope's one model call
    behind a single await, so one slow room would delay every OTHER room's tick.
    `_run_one` below carries the per-scope try/except/finally, and
    `running`/`failures`/`last_failure_at` are read and written from WHICHEVER
    scope's task is executing. This is what makes tick N+1 able to find scope
    "g1" still `running` from tick N — the scenario the lock, the backoff and
    their tests are named for. The per-cycle discipline is watermark-last, the
    fire conditions and the partial-batch guard.

    FOURTH — `adaptors` is the whole population. Each adaptor is scheduled on
    ITS OWN key: `scopes()` for a `key_kind == "scope"` adaptor, `units()` for a
    unit-keyed one (`Conversation.unit`, DEC-105). That is not a wider or
    narrower batch — it is a DIFFERENT key, which is why a single shared
    per-scope watermark could not host one unchanged.

    FIFTH — **failure isolation is PER ADAPTOR, and the backoff key is the
    (adaptor, key) pair** (gate D3). The adaptors have different value
    densities: one that reaches the open web has far more ways to fail than one
    reading its own store, and letting the riskiest silence the cheapest is
    backwards.

    The cost is stated rather than hidden. A provider outage needs one failure
    per (adaptor, key) pair before all of them are backing off, so the first
    tick of an outage costs `len(adaptors) x len(keys)` calls rather than
    `len(scopes)`. That is accepted: each pair still backs off on the same
    capped exponential, and the alternative — one shared key — is precisely the
    coupling this decision exists to remove.
    """
    # Announce, the way `heartbeat` does — a positive liveness signal, so
    # «is the Dreamer running?» is not answered only from a MISSING line. The
    # scope count is here because a loop ticking over ZERO scopes is otherwise
    # indistinguishable from a healthy one.
    log.info(
        # `%g` and not heartbeat's `%.1f`: this tick is configurable down to
        # sub-second in tests and up to minutes in production, and `%.1f`
        # renders a 0.02s tick as "0.0s" — a liveness line that misreports the
        # interval it is announcing is worse than none.
        "dream loop started: tick every %gs over %d scope(s), %d adaptor(s)",
        tick_seconds, len(scopes()), len(adaptors),
    )
    if adaptors and runtime is None:
        # Loud, and at startup rather than at the first tick. An adaptor list
        # with no runtime to run it is a wiring mistake whose only symptom is
        # silence — the loop would tick forever, log nothing, and write nothing,
        # which is indistinguishable from a healthy quiet deployment.
        log.error(
            "dream loop has %d adaptor(s) and no runtime; none of them will run",
            len(adaptors),
        )
    failures: dict[str, int] = {}
    running: set[str] = set()
    # `_backoff_elapsed`'s own per-scope companion state (see its docstring
    # for why it needs a timestamp `failures` alone cannot supply) —
    # function-local to this one `dream_loop` call, exactly like `failures`
    # and `running` beside it.
    last_failure_at: dict[str, float] = {}
    # A scheduled watermark-free adaptor's own clock is DELIBERATELY NOT a
    # dict beside these three. `failures`/`running`/`last_failure_at` are
    # backoff and concurrency state whose correct value after a restart IS
    # empty — a fresh process has no in-flight run and no accrued backoff. A
    # SCHEDULE is the opposite: its correct value after a restart is whatever
    # it was before, and keeping it here would make "daily" mean "once per
    # deploy" (see `_dream_adaptor_once`). It lives in `dream_schedule_state`,
    # reached through `schedule_clock`.
    tasks: set[asyncio.Task[None]] = set()

    def _keys_for(adaptor: DreamAdaptor) -> Sequence[str]:
        """One population per `key_kind`, resolved by the loop.

        An unknown kind returns nothing AND says so: silently ticking such an
        adaptor over the scopes would be a guess about what its rows mean, and
        silently skipping it would be the same subsystem-that-does-nothing
        failure the runtime-less branch above logs.
        """
        if adaptor.key_kind == "scope":
            return scopes()
        if adaptor.key_kind == "unit":
            return units()
        if adaptor.key_kind == "entity":
            # A Character entity: the adaptor
            entity_keys: Callable[[], Sequence[str]] | None = getattr(adaptor, "keys", None)
            if entity_keys is not None:
                return entity_keys()
        log.error(
            "dream adaptor %s declares key_kind=%r, which this loop cannot "
            "enumerate; it will not run", adaptor.name, adaptor.key_kind,
        )
        return ()

    def _count_failure(unit: str) -> None:
        """One place both failure paths converge, so the exponential can never
        be advanced by one and reset by the other."""
        failures[unit] = failures.get(unit, 0) + 1
        last_failure_at[unit] = time.monotonic()

    async def _run_unit(
        adaptor: DreamAdaptor, key: str, runner: _AdaptorRunner, pass_id: str,
    ) -> None:
        """One unit, ONE Langfuse trace, named for its adaptor.

        Each unit is its own root, so the trace list says which adaptor ran;
        the tick's grouping is `nora.dreamer.pass_id`, which
        `TraceIdentityProcessor` stamps as the Langfuse session: one tick is one
        session, one unit is one trace in it. (`dream:cognition`,
        `nora.app.cognition_agent_runner`, is likewise its own root.)

        `attached(None)`, not an inherited context: `create_task` copies
        whatever span is current, and a unit must never nest under it. The
        cursor reads in `_dream_adaptor_once` land inside this root, which
        `DreamerRuntime.run`'s own span could not hold. A unit that fires no
        model call is a trace with no generation, and the collector's tail
        sampler keeps it out of Langfuse (`docs/observability.md`).
        """
        with attached(None), span(
            f"dream:{adaptor.name}",
            **{TRACE_KIND: "generation",
               "nora.dreamer.adaptor": adaptor.name,
               "nora.dreamer.key": key,
               "nora.dreamer.pass_id": pass_id},
        ):
            await _run_adaptor(adaptor, key, runner)

    async def _run_adaptor(
        adaptor: DreamAdaptor, key: str, runner: _AdaptorRunner
    ) -> None:
        """One adaptor's cycle, isolated from every sibling's (DEC-133).

        `runner` is a PARAMETER rather than the enclosing `runtime`, which is
        `_AdaptorRunner | None`: the dispatch below narrows it once, at the one
        place that knows it is set, instead of this function re-asserting a
        condition its caller already established.

        **The WHOLE call to `_dream_adaptor_once` runs under ONE wall clock**
        (`[dreamer] max_unit_hold_seconds`). The store phases (`inputs`,
        `commit`) have their own `wait_for`s; the cursor reads around them —
        `clock.last_fired`/`record_fire`, every `language.*` watermark call,
        `record.since`, `media_join.enrich` — are bare `await`s, and any one of
        them hanging would mean this coroutine never returns,
        `finally: running.discard(unit)` below never runs, and this `unit`
        never ticks again — indistinguishable, on `nora_dream_skipped_total
        {reason="in_flight_adaptor"}`, from an ordinary long run. One bound
        around the whole call, rather than a per-call `wait_for`, also covers a
        call nobody has written yet; the cost is that it can only say WHICH
        ADAPTOR wedged, not which await.
        """
        unit = f"{adaptor.name}:{key}"
        try:
            async with asyncio.timeout(max_unit_hold_seconds) as unit_deadline:
                status = await _dream_adaptor_once(
                    adaptor=adaptor, runtime=runner, record=record,
                    language=language, metrics=metrics, key=key,
                    safety_lag=safety_lag, quiet_seconds=quiet_seconds,
                    max_age_seconds=max_age_seconds, batch_limit=batch_limit,
                    media_join=media_join,
                    schedules=schedules, clock=schedule_clock,
                    # Consecutive failures on THIS unit, so a retry reads less
                    # than the attempt that failed. Cleared on any
                    # non-`unavailable` outcome, so this is 0 in steady state.
                    failure_count=failures.get(unit, 0),
                )
            # On the unit's root (`_run_unit`), so the trace list says what
            # the unit did; `None` is a unit whose conditions held it back.
            annotate(**{"nora.outcome": status or "skipped"})
            if status == "unavailable":
                # **A FAILURE, even though nothing raised.**
                # `DreamerRuntime.run` catches everything the model call can
                # throw — 429 after the retry policy is spent, a reset
                # connection, its own wall clock — and returns
                # `unavailable` NORMALLY. So the `except` below never sees a
                # provider outage; clearing `failures` here would actively
                # CLEAR backoff accrued from an unrelated failure and answer a
                # 429 at full cadence forever, the exact thing this loop's
                # docstring says the backoff exists to prevent.
                #
                # A content failure — the model returned prose — lands here
                # too, and that is accepted rather than special-cased: asking
                # a model that will not answer is also waste, and at a 300s
                # tick the first three backoff steps (60/120/240s) cost
                # nothing. The fourth is where it starts skipping ticks, which
                # is the right point to stop trying.
                _count_failure(unit)
            else:
                failures.pop(unit, None)
                last_failure_at.pop(unit, None)
        except Exception as exc:
            # **ONE `except`, not two.** Python does not chain except clauses
            # on the same try: a `raise` inside a first `except TimeoutError`
            # would propagate PAST a sibling `except Exception:` entirely, out
            # of `_run_adaptor` itself. A collaborator's own `TimeoutError` —
            # `record.since` hitting an asyncpg `command_timeout` or a raw
            # socket timeout, exactly the class `_bounded_store_call`'s own
            # docstring names — is not `asyncio.timeout`'s cancellation, so it
            # is not caught by the `async with` above and lands here. Re-raised,
            # it would be unlogged, unmetered, uncounted by `_count_failure`,
            # and the exponential backoff would never engage. A single clause
            # with an `isinstance` branch cannot skip a sibling, because there
            # is no sibling to skip.
            if isinstance(exc, TimeoutError) and unit_deadline.expired():
                # `[dreamer] max_unit_hold_seconds` fired — OUR wall clock,
                # declared above. `unit_deadline.expired()` is what tells
                # this apart from the `else` branch below: a `TimeoutError`
                # that is not ours is an ORDINARY failure, not a wedge.
                metrics.inc(
                    "nora_dream_unit_timeout_total", adaptor=adaptor.name)
                annotate(**{"nora.outcome": "timeout"})
                log.error(
                    "dream unit timed out after %.0fs (adaptor=%s key=%s) — "
                    "[dreamer] max_unit_hold_seconds fired and the whole "
                    "cycle did not return; the unit is released rather than "
                    "held forever (issue #191)",
                    max_unit_hold_seconds, adaptor.name, key,
                )
                # DELIBERATELY NOT `_count_failure(unit)`. The exponential
                # backoff exists for a provider outage, where retrying at
                # full cadence burns quota against something that is
                # genuinely down. A wedge is a different claim: THIS unit's
                # clock fired, which says nothing about whether a retry right
                # now would wedge again — `record.since` hanging once is not
                # evidence the store is down, only that one call didn't
                # return. Backing it off at `_DREAM_BACKOFF_BASE_SECONDS =
                # 60s` would also defeat the point: a unit released by the
                # wall clock must be tickable on the VERY NEXT tick, not an
                # hour's climb away from it. Existing backoff state is left
                # untouched either way — this outcome is neither a success
                # (clearing it) nor a new failure (extending it).
            else:
                # Everything else: an ordinary exception from the cursor
                # store, the record read, an adaptor raising out of
                # `inputs` — OR a collaborator's own `TimeoutError` that is
                # not `unit_deadline` expiring. Counted against this
                # adaptor's own backoff, never its siblings', and never
                # silently dropped.
                log.exception("dream cycle failed for %s", unit)
                metrics.inc("nora_errors_total", stage="dream")
                # Swallowed here, so `span` never sees it: say it on the root.
                annotate(**{"nora.outcome": "error"})
                _count_failure(unit)
        finally:
            running.discard(unit)

    try:
        while True:
            await asyncio.sleep(tick_seconds)
            _log_instruction_versions(instructions)
            if runtime is None:
                continue
            # ONE ROOT PER UNIT, ONE SESSION PER PASS. A tick's N adaptors x
            # M keys are grouped by the pass id, which each unit's root carries
            # as the Langfuse session. Wall-clock seconds: one tick per id at
            # any `tick_seconds` >= 1, and readable.
            pass_id = "dream.pass:" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for adaptor in adaptors:
                for key in _keys_for(adaptor):
                    # `unit`, not `key`: the lock, the failure count and the
                    # backoff are all per (adaptor, key).
                    unit = f"{adaptor.name}:{key}"
                    if unit in running:
                        # A DISTINCT reason value rather than a new label:
                        # `nora_dream_skipped_total` carries `("reason",)` and
                        # nothing else, and adding a label to a live series
                        # breaks whatever is summing it.
                        metrics.inc(
                            "nora_dream_skipped_total",
                            reason="in_flight_adaptor")
                        continue
                    if failures.get(unit) and not _backoff_elapsed(
                        unit, failures[unit], last_failure_at,
                    ):
                        continue
                    running.add(unit)
                    task = asyncio.create_task(
                        _run_unit(adaptor, key, runtime, pass_id))
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
