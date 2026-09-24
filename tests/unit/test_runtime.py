"""`nora.app.runtime` — the four lifetime concerns `Handler` and `App` used to own
directly (Phase 3, Task 7): the strong-reference background task set, the prune
sweep, the `Debounce` TTL sweep, and per-adapter restart-with-backoff supervision.

Every test here proves *behaviour*, not shape: a coroutine existing is not enough,
each loop must actually run and actually do the thing it claims to.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import time
from dataclasses import dataclass

import pytest

from nora.agent.dreamer import AdaptorSchedule
from nora.app.runtime import (
    BackgroundTasks,
    SupervisedAdapter,
    _backoff_elapsed,
    _dream_adaptor_once,
    _shrunk_batch_limit,
    _slot,
    debounce_sweep_loop,
    dream_loop,
    prune_loop,
    seed_watermarks,
    start_adapters,
    supervise_adapter,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.obs.metrics import Metrics
from nora.tag import Debounce
from nora.testing import InMemoryDreamScheduleClock
from tests.objectives import shipped_objectives

# The `objective` collaborator, wired the way production wires it: the REAL
# `DreamerInstructions` over the REAL `nora/dreamer/` tree, not a double. The
# bodies it serves are byte-identical to the constants these tests were written
# against (`tests/unit/test_dreamer_instructions.py` proves it), so nothing
# below changed meaning — and a broken objective file now fails here rather
# than on the next Dreamer tick.
OBJECTIVES = shipped_objectives()

# ---------------------------------------------------------------------------
# BackgroundTasks
# ---------------------------------------------------------------------------


async def test_spawned_task_survives_gc_because_of_the_strong_reference():
    """The task-tracking test the brief's mandatory probe targets: drop every
    external reference to the spawned task, force a collection, and prove the
    coroutine still runs to completion because `BackgroundTasks` itself holds
    the strong reference asyncio does not."""
    bg = BackgroundTasks()
    ran = False

    async def work() -> None:
        nonlocal ran
        await asyncio.sleep(0.05)
        ran = True

    bg.spawn(work())
    gc.collect()
    await asyncio.sleep(0.2)
    assert ran is True


async def test_a_cancelled_task_is_discarded_without_logging_an_error(caplog):
    bg = BackgroundTasks()
    caplog.set_level(logging.ERROR, logger="nora.app.runtime")
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(10)

    task = bg.spawn(work())
    await started.wait()
    task.cancel()
    await asyncio.wait([task])

    assert len(bg) == 0
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_a_failing_task_logs_and_is_discarded(caplog):
    bg = BackgroundTasks()
    caplog.set_level(logging.ERROR, logger="nora.app.runtime")

    async def work() -> None:
        raise ValueError("boom")

    task = bg.spawn(work())
    await asyncio.wait([task])

    assert len(bg) == 0
    assert any("background task failed" in r.message for r in caplog.records)


async def test_cancel_all_cancels_every_tracked_task():
    bg = BackgroundTasks()

    async def work() -> None:
        await asyncio.sleep(10)

    bg.spawn(work())
    bg.spawn(work())
    assert len(bg) == 2

    await bg.cancel_all()
    assert len(bg) == 0


# ---------------------------------------------------------------------------
# prune_loop
# ---------------------------------------------------------------------------


async def test_prune_loop_survives_a_failing_sweep_and_keeps_looping():
    """Proves the sweep actually runs (not just that the coroutine exists), and
    that a failure on one iteration does not stop the next one from happening."""

    class FailFirstThenOkStore:
        def __init__(self) -> None:
            self.calls = 0
            self.kept_looping = asyncio.Event()

        async def prune_all(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            if self.calls >= 3:
                self.kept_looping.set()
            return 3

    store = FailFirstThenOkStore()
    metrics = Metrics()

    task = asyncio.create_task(prune_loop(store, metrics, interval_seconds=0.01))
    # The exact twin of the debounce test above, converted for the same reason:
    # `sleep(0.08)` over `interval_seconds=0.01` is a wall-clock assumption, and
    # this name promises "keeps looping" while the old assertion was satisfied
    # by a loop that ran twice and died.
    try:
        await asyncio.wait_for(store.kept_looping.wait(), timeout=5.0)
    except TimeoutError:  # pragma: no cover - only on a real regression
        pytest.fail(
            "the prune loop did not keep looping after a failing sweep; "
            f"calls={store.calls}, expected at least 3"
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert store.calls >= 3, "the loop must keep running after a failing sweep"
    assert metrics.get("nora_errors_total", stage="prune") == 1


# ---------------------------------------------------------------------------
# debounce_sweep_loop
# ---------------------------------------------------------------------------


async def test_debounce_sweep_loop_actually_removes_stale_entries():
    """Asserts the dict shrinks, not that the coroutine exists."""
    clock = {"t": 0.0}
    d = Debounce(1.0, clock=lambda: clock["t"])
    d.allow("a")
    d.allow("b")
    assert len(d._last) == 2

    clock["t"] = 100.0  # both entries are now far past any reasonable ttl
    metrics = Metrics()

    task = asyncio.create_task(
        debounce_sweep_loop(d, metrics, interval_seconds=0.01, ttl_seconds=5.0)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(d._last) == 0


async def test_debounce_sweep_loop_survives_a_failing_sweep():
    calls = {"n": 0}
    kept_sweeping = asyncio.Event()
    real_sweep = Debounce.sweep

    class ExplodingDebounce(Debounce):
        def sweep(self, older_than: float) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            if calls["n"] >= 3:
                kept_sweeping.set()
            return real_sweep(self, older_than)

    d = ExplodingDebounce(1.0)
    metrics = Metrics()

    task = asyncio.create_task(
        debounce_sweep_loop(d, metrics, interval_seconds=0.01, ttl_seconds=5.0)
    )
    # Wait for the CONDITION, never for a wall-clock window. The previous form
    # slept 0.05s and asserted two sweeps had landed at 0.01s intervals — true
    # on an idle loop, false on a loaded one, which is why it went red in full
    # runs and green alone (#170). Blocking the loop in 25ms slices reproduces
    # it every time. The timeout is a deadlock guard, not a measurement: the
    # claim is that the loop recovers, and how fast was never the point.
    #
    # It waits for the THIRD sweep, not the second. "Survives" and "keeps
    # sweeping" is what the name promises, and a loop that resumes once and
    # then returns forever satisfied the old assertion — verified: injecting
    # `return` after two rounds left both the old and the first new form green
    # in 0.07s against a permanently dead loop.
    try:
        await asyncio.wait_for(kept_sweeping.wait(), timeout=5.0)
    except TimeoutError:  # pragma: no cover - only on a real regression
        pytest.fail(
            "the sweep loop did not keep sweeping after a failing sweep; "
            f"sweeps={calls['n']}, expected at least 3"
        )
    finally:
        # In `finally` so a timeout does not leak the task and orphan the real
        # exception into stderr, which is what the first version of this did.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls["n"] >= 3
    assert metrics.get("nora_errors_total", stage="debounce_sweep") == 1


# ---------------------------------------------------------------------------
# adapter supervision
# ---------------------------------------------------------------------------


async def test_a_failing_adapter_is_restarted_and_counted():
    metrics = Metrics()
    calls = 0

    async def flaky_listen() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("wedged")

    adapter = SupervisedAdapter(platform="signal", listen=flaky_listen)
    task = asyncio.create_task(
        supervise_adapter(adapter, metrics, base_delay_seconds=0.01, max_delay_seconds=0.02)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls >= 2, "the adapter must be restarted after it fails"
    assert metrics.get("nora_adapter_restarts_total", platform="signal") >= 1


async def test_a_second_adapter_keeps_running_while_the_first_fails():
    metrics = Metrics()
    bg = BackgroundTasks()
    bad_calls = 0
    good_calls = 0
    good_running = asyncio.Event()

    async def bad_listen() -> None:
        nonlocal bad_calls
        bad_calls += 1
        raise RuntimeError("wedged")

    async def good_listen() -> None:
        nonlocal good_calls
        good_calls += 1
        good_running.set()
        await asyncio.sleep(10)

    adapters = [
        SupervisedAdapter(platform="bad", listen=bad_listen),
        SupervisedAdapter(platform="good", listen=good_listen),
    ]
    tasks = start_adapters(bg, adapters, metrics)
    assert len(bg) == 2

    await good_running.wait()
    await asyncio.sleep(0.05)

    assert bad_calls >= 1
    assert metrics.get("nora_adapter_restarts_total", platform="bad") >= 1
    assert good_calls == 1, "the healthy adapter must not have been restarted"
    assert metrics.get("nora_adapter_restarts_total", platform="good") == 0

    for t in tasks:
        t.cancel()
    await asyncio.wait(tasks)
    assert len(bg) == 0


# ---------------------------------------------------------------------------
# dream_loop
# ---------------------------------------------------------------------------


def _row(id: int) -> TranscriptRow:
    return TranscriptRow(
        id=id, sender_id="+852111", sender_name="Miles", ts=1_000 + id, body=f"m{id}",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


class _FakeRecord:
    """Stands in for the `_SinceReadable` slice of `RecordStore` — one
    scope's fixed batch, returned whole regardless of `after_id`/`limit`
    (nothing here exercises pagination; that is `RecordStore.since`'s own
    contract, proven in `tests/infra/test_audit_store.py`)."""

    def __init__(self, batches: dict[str, list[TranscriptRow]]) -> None:
        self._batches = batches

    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """`id > after_id`, ascending, at most `limit` — the real one's
        contract, HONOURED rather than ignored.

        This returned the whole batch whatever `after_id` was, so the
        consolidation cursor was invisible here: setting
        `nora/app/runtime.py`'s `after_id=watermark` to `after_id=0` — every
        dream tick re-reading the conversation from the beginning and
        re-feeding what it already consolidated — left the entire suite green
        (6142 passed, verified 2026-09-09). That line is the only `.since(`
        call site in `nora/`, so nothing else could have caught it.
        """
        rows = [r for r in self._batches.get(conversation_id, []) if r.id > after_id]
        return sorted(rows, key=lambda r: r.id)[:limit]


class _FakeLanguage:
    """Stands in for `SqlLanguageStore`'s cursor/measurement surface.
    `lag`/`quiet`/`age`/`behind` are fixed per test (not derived from any
    real message table) — each test sets exactly the ones its own fire
    condition needs. `advance_calls` records EVERY `advance_watermark` call
    in order, and `order` (when given) lets a test interleave that against a
    consolidator's own call order — the watermark-last proof this fake
    exists for."""

    def __init__(
        self, *, lag: int = 0, quiet: float = 0.0, age: float = 0.0,
        behind: bool = False, order: list[str] | None = None,
    ) -> None:
        self._lag = lag
        self._quiet = quiet
        self._age = age
        self._behind = behind
        self._order = order
        self.watermarks: dict[str, int] = {}
        self.advance_calls: list[tuple[str, int]] = []
        # `_dream_once` calls `watermark_for` FIRST, unconditionally, on
        # EVERY tick a scope is considered — whether or not it goes on to
        # fire. A count on this is an independent WITNESS that N ticks
        # actually elapsed during a test's `asyncio.sleep` window, distinct
        # from (and not derivable from) whatever the fire conditions
        # themselves produced — see `test_the_daily_firing_...` for why a
        # test needs this to avoid passing vacuously on a single tick.
        self.watermark_for_calls = 0
        # Which scopes have had `advance_watermark` called at least once —
        # the real store's `updated_at` column, reduced to a boolean. Needed
        # for `watermark_age_seconds` below: the real bug this fake exists to
        # catch is a stale `updated_at` that never resets, so THIS fake must
        # actually reset something when `advance_watermark` runs, or a test
        # against it could not tell "the fix runs" from "the fix does
        # nothing" any better than the real store's own age column could.
        self._age_reset_scopes: set[str] = set()

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int:
        self.watermark_for_calls += 1
        return self.watermarks.get(scope_id, 0)

    async def watermark_is_behind_the_record(self, *, scope_id: str, watermark: int) -> bool:
        return self._behind

    async def lag(self, *, scope_id: str, after_id: int) -> int:
        # Once THIS scope's watermark has actually MOVED (advanced past 0),
        # treat its configured lag as consumed — otherwise a test whose fake
        # batch never grows would keep firing forever at the tick cadence,
        # muddying an assertion that means to observe exactly one cycle. The
        # empty-batch age-refresh case (Task 11 fix) calls `advance_watermark`
        # with `last_message_id` UNCHANGED (often 0), so it alone must not
        # trip this — `watermarks.get(...) > 0` already only fires on a REAL
        # forward move, never on the refresh-in-place case.
        if self.watermarks.get(scope_id, 0) > 0:
            return 0
        return self._lag

    async def seconds_since_last_message(self, *, scope_id: str) -> float:
        return self._quiet

    async def watermark_age_seconds(self, *, consolidator_name: str, scope_id: str) -> float:
        # Mirrors the real store: `updated_at` (hence age) resets to "now"
        # the moment `advance_watermark` runs for this scope, REGARDLESS of
        # whether `last_message_id` actually moved — the empty-batch refresh
        # (Task 11 fix) relies on exactly this to stop the 24h firing from
        # relatching every tick.
        if scope_id in self._age_reset_scopes:
            return 0.0
        return self._age

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None:
        if self._order is not None:
            self._order.append("advance")
        self.advance_calls.append((scope_id, last_message_id))
        self.watermarks[scope_id] = last_message_id
        self._age_reset_scopes.add(scope_id)


# ---------------------------------------------------------------------------
# dream_loop backoff (non-negotiable 4)
# ---------------------------------------------------------------------------


def test_backoff_elapsed_grows_exponentially_and_caps():
    """`_backoff_elapsed`'s own arithmetic — `supervise_adapter`'s
    capped-exponential shape, reused rather than re-derived. Pins the two
    things that shape means: MORE consecutive failures need MORE elapsed
    time, and the delay stops growing at `max_delay_seconds`."""
    last_failure_at: dict[str, float] = {"g1": time.monotonic()}

    # One failure: not enough time has passed even for the smallest delay.
    assert _backoff_elapsed(
        "g1", 1, last_failure_at, base_delay_seconds=0.05, max_delay_seconds=10.0,
    ) is False
    time.sleep(0.06)
    assert _backoff_elapsed(
        "g1", 1, last_failure_at, base_delay_seconds=0.05, max_delay_seconds=10.0,
    ) is True

    # Five consecutive failures: 0.05 * 2**4 = 0.8s uncapped, but capped here
    # at 0.1s — so 0.06s must still be "not elapsed" (proves the delay GREW
    # past the one-failure case above) and 0.11s must be "elapsed" (proves
    # the cap, not the uncapped 0.8s, is what actually bounds it).
    last_failure_at["g1"] = time.monotonic()
    assert _backoff_elapsed(
        "g1", 5, last_failure_at, base_delay_seconds=0.05, max_delay_seconds=0.1,
    ) is False
    time.sleep(0.06)
    assert _backoff_elapsed(
        "g1", 5, last_failure_at, base_delay_seconds=0.05, max_delay_seconds=0.1,
    ) is False, "five failures must need MORE than the one-failure delay"
    time.sleep(0.05)
    assert _backoff_elapsed(
        "g1", 5, last_failure_at, base_delay_seconds=0.05, max_delay_seconds=0.1,
    ) is True, "but the delay must be CAPPED at max_delay_seconds, not 0.05 * 2**4"


async def test_the_dream_loop_says_it_started(caplog):
    """The Dreamer was the ONE background loop with no positive liveness signal.

    Every sibling announces itself (`heartbeat started: tick every 60.0s`,
    `nora/app/heartbeat.py`), and `__main__` logs only the DISABLED branch — so
    with `dreamer.enabled = true` the logs said nothing at all, and «is it
    running?» could only be answered from a MISSING line. That is an absence
    argument about the slowest loop in the system.

    It logs from inside the loop, not from the spawn site, so it reports that
    the loop is TICKING rather than that a branch was taken. The scope count is
    in it because zero scopes is the quiet failure this cannot otherwise show:
    a loop that runs forever over nothing looks identical to a healthy one.
    """
    record = _FakeRecord({})
    language = _FakeLanguage(lag=1000)

    with caplog.at_level(logging.INFO, logger="nora.app.runtime"):
        task = asyncio.create_task(dream_loop(
            record, language, Metrics(),
            scopes=lambda: ["g1", "g2"], tick_seconds=0.02, safety_lag=10,
            quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    started = [r.getMessage() for r in caplog.records if "dream loop started" in r.getMessage()]
    assert started, "the dream loop must announce itself the way heartbeat does"
    assert "0.02" in started[0]      # the tick it will actually keep
    assert "2" in started[0]         # the scopes it can see
    assert len(started) == 1, "announced once at start, not on every tick"


# --- the per-tick instruction-versions line (substrate spec 4.4) -----------


class _FakeInstructions:
    """The `_InstructionVersions` shape, plus a lever for the failure path."""

    def __init__(self, versions=None, fail: bool = False) -> None:
        self._versions = versions or {"dreamer": 1, "room-language": 2}
        self._fail = fail
        self.calls = 0

    def versions(self) -> dict[str, int]:
        self.calls += 1
        if self._fail:
            raise OSError("the bind mount went away")
        return dict(self._versions)


async def test_a_tick_logs_the_instruction_revision_it_is_running(caplog):
    """Spec 4.4: a Dreamer tick names its instruction versions the way a tag
    start logs `doctrine_versions={...}`, so a trace ties to a revision without
    anyone opening the database. Without it the only way to ask "which
    objective wrote these rows" is a SQL query against a hash."""
    instructions = _FakeInstructions({"dreamer": 1, "note-curation": 3})
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": []}), _FakeLanguage(lag=0), Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        instructions=instructions,
    ))
    with caplog.at_level(logging.INFO):
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    lines = [r.getMessage() for r in caplog.records if "instruction_versions" in r.getMessage()]
    assert lines
    assert "'dreamer': 1" in lines[0]
    assert "'note-curation': 3" in lines[0]


async def test_a_broken_instruction_corpus_cannot_take_the_clock_down(caplog):
    """The line reads a HOT-RELOADING corpus off a bind mount, and it runs
    OUTSIDE `dream_loop`'s per-scope try/except — so an uncaught exception here
    would stop the Dreamer's clock for the life of the process, in the name of
    a log line. Warned rather than swallowed: an operator has to be able to see
    that the corpus stopped being readable."""
    instructions = _FakeInstructions(fail=True)
    runner = _RecordingRunner()
    adaptor = _StubAdaptor("note-curation")
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": [_row(1)]}), _KeyedLanguage(lag=1000),
        Metrics(), scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        instructions=instructions, adaptors=[adaptor], runtime=runner,
    ))
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.15)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert instructions.calls > 1, "the loop kept ticking after the failure"
    assert runner.calls, "the Dreamer kept working"
    assert any("instruction versions unavailable" in r.getMessage() for r in caplog.records)


async def test_no_instruction_source_logs_no_line_and_changes_nothing(caplog):
    """`instructions` is optional so every existing caller and test keeps
    working unchanged; absent, the loop is byte-for-byte what it was."""
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": []}), _FakeLanguage(lag=0), Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    with caplog.at_level(logging.INFO):
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert not [r for r in caplog.records if "instruction_versions" in r.getMessage()]


# ===========================================================================
# per-adaptor scheduling (substrate plan Task 2.8, gate D3)
#
# The loop that carries live traffic gains a second, additive schedule. Every
# test below asserts one of the four properties that fail QUIETLY if they are
# wrong: the isolation boundary, the backoff key that must move with it, the
# watermark that must not advance past a batch nobody read, and the key
# population an adaptor is actually ticked over.
# ===========================================================================


class _KeyedLanguage:
    """`_FakeLanguage`'s per-adaptor sibling: keys everything on
    `(consolidator_name, scope_id)` rather than on the scope alone.

    A separate fake rather than a widened one. `_FakeLanguage` is shared by
    two dozen tests of the consolidator path, where the cursor name is the
    hardcoded `"language"` and keying on it would change nothing — widening it
    would edit those tests' collaborator to prove a property they do not have.
    This one exists precisely because per-adaptor scheduling makes the name
    load-bearing, and it must be able to FAIL when two adaptors share a row.
    """

    def __init__(self, *, lag: int = 0, quiet: float = 0.0, age: float = 0.0) -> None:
        self._lag = lag
        self._quiet = quiet
        self._age = age
        self.watermarks: dict[tuple[str, str], int] = {}
        self.advance_calls: list[tuple[str, str, int]] = []
        self._advanced: set[tuple[str, str]] = set()
        #: Per-scope newest message id, for `seed_watermarks`' head fallback.
        #: Absent means a scope with no messages at all, which is a real state
        #: and must not be seeded past.
        self.heads: dict[str, int] = {}

    async def head_message_id(self, *, scope_id: str) -> int:
        return self.heads.get(scope_id, 0)

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int:
        return self.watermarks.get((consolidator_name, scope_id), 0)

    async def watermark_is_behind_the_record(
        self, *, scope_id: str, watermark: int
    ) -> bool:
        return False

    async def lag(self, *, scope_id: str, after_id: int) -> int:
        return 0 if after_id > 0 else self._lag

    async def seconds_since_last_message(self, *, scope_id: str) -> float:
        return self._quiet

    async def watermark_age_seconds(
        self, *, consolidator_name: str, scope_id: str
    ) -> float:
        if (consolidator_name, scope_id) in self._advanced:
            return 0.0
        return self._age

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None:
        self.advance_calls.append((consolidator_name, scope_id, last_message_id))
        self.watermarks[(consolidator_name, scope_id)] = last_message_id
        self._advanced.add((consolidator_name, scope_id))


class _StubAdaptor:
    def __init__(
        self, name: str, *, key_kind: str = "scope", uses_watermark: bool = True,
    ) -> None:
        self.name = name
        self.key_kind = key_kind
        self.uses_watermark = uses_watermark
        self.objective = "note-curation"
        self.envelope_key = "observations"
        self.tools: frozenset[str] = frozenset()
        # `DreamAdaptor.store_calls_per_tick` (issue #60): required, so a
        # stub that omitted it would not satisfy the protocol.
        self.store_calls_per_tick = 1

    async def inputs(self, *, key, batch):        # pragma: no cover - unused
        return ""

    async def accept(self, *, key, items):        # pragma: no cover - unused
        return ()

    async def commit(self, *, key, proposals, now_ms):   # pragma: no cover
        return 0


@dataclass
class _StubOutcome:
    status: str = "ok"
    written: int = 1


class _RecordingRunner:
    """Records every `(adaptor, key)` it was asked to run, and can be told to
    fail — or to report `unavailable` — for one named adaptor."""

    def __init__(
        self, *, status: str = "ok", raises_for: str | None = None,
        unavailable_for: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self._status = status
        self._raises_for = raises_for
        self._unavailable_for = unavailable_for

    async def run(self, *, adaptor, key, batch):
        self.calls.append((adaptor.name, key, len(batch)))
        if adaptor.name == self._raises_for:
            raise RuntimeError("the store is down")
        if adaptor.name == self._unavailable_for:
            return _StubOutcome(status="unavailable", written=0)
        return _StubOutcome(status=self._status)


async def _tick(loop_coro, *, ticks: float = 0.06):
    task = asyncio.create_task(loop_coro)
    await asyncio.sleep(ticks)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class _SpanningRunner:
    """Opens the SAME shape `DreamerRuntime.run` does (`nora/agent/dreamer.py`)
    around each call — a root-declaring `dream:{adaptor.name}` span — without
    dragging that whole collaborator graph (models/instructions/tools/store
    timeouts) into a test of `dream_loop`'s OWN dispatch shape. `gate` holds
    every call open until the test releases it, which is what makes the
    per-tick lock (`unit in running`) deterministic here: every tick before
    `gate.set()` sees each (adaptor, key) unit still in flight and skips it,
    so exactly one task — and one child span — is ever created per unit,
    regardless of how many ticks fire in the meantime.
    """

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate
        self.calls: list[tuple[str, str]] = []

    async def run(self, *, adaptor, key, batch):
        from nora.obs.tracing import TRACE_KIND, span

        with span(
            f"dream:{adaptor.name}",
            **{TRACE_KIND: "generation", "nora.dreamer.adaptor": adaptor.name,
               "nora.dreamer.key": key},
        ):
            self.calls.append((adaptor.name, key))
            await self._gate.wait()
            return _StubOutcome(status="ok")


async def test_dream_pass_opens_one_root_with_children_per_adaptor(exported_spans):
    """Issue #55's third unlit boundary: THE LOOP. `DreamerRuntime.run`'s own
    span declares itself a root (R-76) for the standalone callers that reach
    it with no pass around them at all — so before `dream_loop` opened
    anything, every (adaptor, key) pair in one tick rendered as its own
    unrelated trace. One tick over two adaptors must now be ONE trace: one
    `dream.pass` root, two `dream:*` children.

    PROBE: delete the `with span("dream.pass", ...)` wrapper around the
    dispatch loop in `dream_loop` and the two `dream:*` spans below become
    roots of two SEPARATE traces instead of children of one.
    """
    language = _KeyedLanguage(lag=99)
    gate = asyncio.Event()
    runner = _SpanningRunner(gate)

    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": [_row(1)]}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes"), _StubAdaptor("notes")], runtime=runner,
    ))
    await asyncio.sleep(0.05)      # several ticks, held open by the gate
    gate.set()
    await asyncio.sleep(0.02)      # let the gated runs finish and close their spans
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert sorted(runner.calls) == [("lexemes", "g1"), ("notes", "g1")]

    finished = exported_spans.get_finished_spans()
    by_name: dict[str, list] = {}
    for recorded in finished:
        by_name.setdefault(recorded.name, []).append(recorded)

    # EVERY tick opens its own `dream.pass` (several fire inside the gated
    # window above — see the in-flight lock discussion), and every one of
    # them is a genuine root: an empty pass is exactly the "ends visibly, at
    # a named stage, even with nothing dispatched" property, not noise to
    # filter out. Only the FIRST one actually dispatched work, so that is the
    # one both children must point at.
    assert by_name["dream.pass"], "no pass was ever opened"
    assert all(p.parent is None for p in by_name["dream.pass"])
    assert len(by_name["dream:lexemes"]) == 1
    assert len(by_name["dream:notes"]) == 1
    lexemes = by_name["dream:lexemes"][0]
    notes = by_name["dream:notes"][0]
    pass_roots = {p.context.span_id: p for p in by_name["dream.pass"]}
    assert lexemes.parent.span_id in pass_roots
    assert lexemes.parent.span_id == notes.parent.span_id, \
        "both adaptors of ONE tick must share ONE pass root"
    pass_root = pass_roots[lexemes.parent.span_id]
    assert lexemes.context.trace_id == pass_root.context.trace_id
    assert notes.context.trace_id == pass_root.context.trace_id


async def test_an_adaptor_is_ticked_over_the_scopes_when_it_is_scope_keyed():
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1)], "g2": [_row(2)]}), language, Metrics(),
        scopes=lambda: ["g1", "g2"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes")], runtime=runner,
    ))

    assert {(a, k) for a, k, _ in runner.calls} == {("lexemes", "g1"), ("lexemes", "g2")}


async def test_a_unit_keyed_adaptor_is_ticked_once_per_unit():
    """Not a wider or narrower batch — a DIFFERENT key. Two rooms of one
    unit must produce ONE tick, or the fact tier adjudicates the same
    claim once per room and calls it corroboration."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1", "g2"], units=lambda: ["nora"],
        tick_seconds=0.01, safety_lag=0, quiet_seconds=0.0,
        max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts", key_kind="unit", uses_watermark=False)],
        runtime=runner,
    ))

    assert {(a, k) for a, k, _ in runner.calls} == {("facts", "nora")}


async def test_an_unknown_key_kind_runs_nothing_and_says_so(caplog):
    """Silently ticking it over the scopes would be a guess about what its rows
    mean; silently skipping it is a subsystem that does nothing and reports
    nothing."""
    runner = _RecordingRunner()

    with caplog.at_level(logging.ERROR):
        await _tick(dream_loop(
            _FakeRecord({}), _KeyedLanguage(), Metrics(),
            scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
            quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
            adaptors=[_StubAdaptor("odd", key_kind="galaxy")], runtime=runner,
        ))

    assert runner.calls == []
    assert "key_kind='galaxy'" in caplog.text


async def test_a_watermarkless_adaptor_gets_an_empty_batch_and_no_cursor_read():
    """`RoomPortraitSynthesizer`'s posture, generalised: no watermark, no lag,
    no quiet, no age. Asserted through the CURSOR — a fake that recorded no
    calls proves the loop never asked, which is the property; a passing run
    alone would not."""
    language = _KeyedLanguage(age=1e9)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1)]}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts", uses_watermark=False)], runtime=runner,
    ))

    assert runner.calls[0] == ("facts", "g1", 0)
    # Filtered to THIS adaptor's own cursor: the consolidator schedule keeps
    # its `"language"` row on the same tick, and asserting over every row would
    # make this test about the legacy path instead of about this one.
    assert [c for c in language.advance_calls if c[0] == "facts"] == []


# ---- AdaptorSchedule, universal (32 §3.1 for the watermark-bearing,
# 33 §5 N1a for the watermark-free)


async def test_a_scheduled_watermark_free_adaptor_fires_once_then_holds_for_the_interval():
    """The non-watermark schedule path: the FIRST tick fires (nothing has
    fired yet) and every later tick inside `min_interval_seconds` does not.
    No `lag > 0` requirement — this fake reports zero lag throughout, and the
    fire happens anyway, which is what makes a silent room's notes retire on
    the calendar rather than on backlog."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
        schedule_clock=InMemoryDreamScheduleClock(),
    ), ticks=0.08)

    assert [c for c in runner.calls if c[0] == "curator"] == [("curator", "g1", 0)]


class _CountingRunner(_RecordingRunner):
    """`_RecordingRunner` plus an `asyncio.Event` set the moment `curator`'s
    SECOND call lands — the condition
    `test_a_scheduled_watermark_free_adaptor_fires_again_after_the_interval`
    below waits on, instead of guessing how long two fires take."""

    def __init__(self) -> None:
        super().__init__()
        self.curator_fired_twice = asyncio.Event()

    async def run(self, *, adaptor, key, batch):
        outcome = await super().run(adaptor=adaptor, key=key, batch=batch)
        if adaptor.name == "curator" and len(
            [c for c in self.calls if c[0] == "curator"]
        ) >= 2:
            self.curator_fired_twice.set()
        return outcome


async def test_a_scheduled_watermark_free_adaptor_fires_again_after_the_interval():
    """The other half of the clock: the interval elapses and the adaptor
    fires again.

    The previous form slept a fixed 0.12s and asserted `2 <= len(calls) < 12`
    — the only TWO-SIDED count assertion in the suite (#180). The lower bound
    needed ~0.09s inside that window (3x margin); the upper bound had ZERO
    margin: `_slot` buckets on REAL wall-clock time (`_now_ms`, not
    `monotonic()` — see its own docstring), so under load a nominally-0.12s
    `asyncio.sleep` can run for far longer than 0.12s, the slot can advance
    more than twelve times over that stretched real interval, and the bound
    fails from the OTHER direction too.

    Converted to wait on the CONDITION — the second fire — guarded by a
    generous timeout rather than measured by one. Cancelling the instant the
    second fire lands makes the old upper bound structural instead of a magic
    constant: nothing more can accumulate once the task is dead.

    What the count bound was ALSO standing in for — that this is a genuine
    interval re-fire and not the adaptor running on every tick — is
    deliberately NOT re-proven here by a second wall-clock measurement. The
    first attempt at this fix tried exactly that: assert
    `nora_dream_skipped_total{reason="schedule"} > 0` (the schedule's own
    "a tick was held" counter, incremented at
    `nora/app/runtime.py::_dream_adaptor_once`) between the two fires. MEASURED
    rather than assumed correct: 8 solo reruns, 3 failures — because whether
    ANY tick lands inside the ~20ms window between the first fire and the
    slot boundary depends on where in its 30ms slot the first fire happens to
    land, which is exactly the kind of real-clock phase alignment this issue
    exists to stop testing against. Re-deriving "holds between fires" from
    wall-clock phase would have reintroduced #180's own failure mode inside
    its own fix.

    That property is proven WITHOUT a clock instead, by two sibling tests:
    `test_a_scheduled_watermark_free_adaptor_fires_once_then_holds_for_the_interval`
    (a schedule holds an adaptor open-ended, not just once) and
    `test_a_watermark_free_adaptor_without_a_schedule_still_runs_every_tick`
    (the contrasting, unscheduled "every tick" case the schedule exists to
    prevent) — plus `test_two_adaptors_sharing_an_interval_can_hold_different_
    phases`, which exercises `_slot` as a pure function with fixed
    millisecond inputs, no clock at all. This test's own job shrinks to the
    one claim its name makes: fires again.
    """
    language = _KeyedLanguage()
    metrics = Metrics()
    runner = _CountingRunner()

    task = asyncio.create_task(dream_loop(
        _FakeRecord({}), language, metrics,
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=0.03, batch_limit=10)},
        schedule_clock=InMemoryDreamScheduleClock(),
    ))
    try:
        await asyncio.wait_for(runner.curator_fired_twice.wait(), timeout=5.0)
    except TimeoutError:  # pragma: no cover - only on a real regression
        calls = [c for c in runner.calls if c[0] == "curator"]
        pytest.fail(
            "the scheduled adaptor did not fire a second time; "
            f"fires={len(calls)}"
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    calls = [c for c in runner.calls if c[0] == "curator"]
    assert len(calls) >= 2, "must fire again, not just once"


async def test_a_watermark_free_adaptor_without_a_schedule_still_runs_every_tick():
    """The invariant the schedule path must not disturb: NO entry in the
    mapping is every-tick, exactly as before schedules existed."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
    ), ticks=0.08)

    assert len([c for c in runner.calls if c[0] == "curator"]) > 1


async def test_a_scheduled_watermark_bearing_adaptor_still_needs_backlog_and_age():
    """32 §3.1, pinned against the reorder: the schedule moving BEFORE the
    watermark early return must not shift the watermark-bearing shape —
    `lag > 0 and age >= min_interval_seconds`, both halves required."""
    runner = _RecordingRunner()
    schedules = {"facts": AdaptorSchedule(min_interval_seconds=1e9, batch_limit=10)}

    # Backlog but a YOUNG row: the interval has not elapsed, so no fire.
    young = _KeyedLanguage(lag=99, age=0.0)
    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1)]}), young, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner, schedules=schedules,
    ))
    assert [c for c in runner.calls if c[0] == "facts"] == []

    # An OLD row but no backlog: the daily batch is data-gated, so no fire.
    quiet_room = _KeyedLanguage(lag=0, age=1e9)
    await _tick(dream_loop(
        _FakeRecord({}), quiet_room, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner, schedules=schedules,
    ))
    assert [c for c in runner.calls if c[0] == "facts"] == []

    # Both: fires, and on the schedule's own batch limit.
    due = _KeyedLanguage(lag=99, age=1e9)
    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1), _row(2)]}), due, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner, schedules=schedules,
    ))
    assert ("facts", "g1", 2) in runner.calls


async def test_a_scheduled_watermark_free_adaptor_tracks_last_fired_per_key():
    """The clock is keyed `(adaptor.name, key)` (33 §5 N1a) — one unit's
    curator firing must never hold back or advance another's. Both scopes fire
    on the first tick (nothing has fired yet for either key), and neither
    refires inside the interval."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1", "g2"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
        schedule_clock=InMemoryDreamScheduleClock(),
    ), ticks=0.08)

    calls = [c for c in runner.calls if c[0] == "curator"]
    assert calls.count(("curator", "g1", 0)) == 1
    assert calls.count(("curator", "g2", 0)) == 1


async def test_a_scheduled_adaptor_does_not_refire_after_a_restart():
    """**The defect this clock exists for.** The schedule used to live in a
    `time.monotonic()` dict inside `dream_loop`'s frame, so a restart began
    with an empty clock and the next tick always fired. `nora` is
    force-recreated several times on an ordinary day, which made a daily
    adaptor a per-deploy one — and `nora_dream_run_total` cannot tell the two
    apart, so nothing would have caught it.

    Two SEPARATE `dream_loop` calls are the restart: each builds its own
    frame, and only the clock crosses between them.

    Falsification: move the clock back into the loop's frame (or give each
    call its own `InMemoryDreamScheduleClock`) and the count becomes 2.
    """
    clock = InMemoryDreamScheduleClock()
    runner = _RecordingRunner()
    schedules = {"curator": AdaptorSchedule(
        min_interval_seconds=1e9, batch_limit=10)}

    for _ in range(2):
        await _tick(dream_loop(
            _FakeRecord({}), _KeyedLanguage(), Metrics(),
            scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
            quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
            adaptors=[_StubAdaptor("curator", uses_watermark=False)],
            runtime=runner, schedules=schedules, schedule_clock=clock,
        ), ticks=0.04)

    assert [c for c in runner.calls if c[0] == "curator"].count(
        ("curator", "g1", 0)) == 1


async def test_an_unavailable_run_does_not_burn_the_slot():
    """**R-27 applied to the schedule itself: an ERROR is never a PASS.**

    `DreamerRuntime.run` absorbs a 429 that outlived the retry policy, a
    timeout, or a killed loop and returns `unavailable` NORMALLY. An earlier
    version of this branch stamped the clock BEFORE the run, so any of those
    burned the whole day with no retry — and the backoff could not compensate,
    because a schedule hold returns `None`, `None` is not `unavailable`, and
    `dream_loop` clears `failures` on it. It also wrote `last_fired_at` for a
    run that did nothing, into the one table an operator reads to ask whether
    the clock is alive.

    So the slot stays open on `unavailable` and the next tick retries inside
    the same day.

    Falsification: move `record_fire` back above `runtime.run`, or drop the
    `status != "unavailable"` guard — the clock stamps and the retry stops.
    """
    clock = InMemoryDreamScheduleClock()
    runner = _RecordingRunner(unavailable_for="curator")

    await _tick(dream_loop(
        _FakeRecord({}), _KeyedLanguage(), Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
        schedule_clock=clock,
    ), ticks=0.05)

    # POSITIVE CONTROL first: `is None` below is also true of a loop that
    # never dispatched the adaptor at all, which is a different bug wearing
    # the same assertion.
    assert [c for c in runner.calls if c[0] == "curator"] == [("curator", "g1", 0)]
    assert await clock.last_fired(adaptor_name="curator", key="g1") is None, (
        "an unavailable run must leave the slot unclaimed"
    )
    # NOT asserting a retry within these ticks: `unavailable` also feeds
    # `dream_loop`'s exponential backoff, which is exactly what should pace
    # the retry. The property here is that the SCHEDULE is not what blocks it
    # — the slot is still open when the backoff lets go.


async def test_a_successful_run_does_claim_the_slot():
    """The positive control for the test above. `== None` is also true of a
    clock nothing ever wrote to, so the retry assertion alone cannot tell
    "held open on failure" from "the stamp is broken for everything"."""
    clock = InMemoryDreamScheduleClock()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), _KeyedLanguage(), Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
        schedule_clock=clock,
    ), ticks=0.05)

    assert await clock.last_fired(adaptor_name="curator", key="g1") is not None
    assert len([c for c in runner.calls if c[0] == "curator"]) == 1


async def test_a_schedule_hold_is_counted():
    """A hold must be visible. The defect this mechanism fixes survived eleven
    days because `nora_dream_run_total` counts runs and not their reason — a
    schedule that never fires and a tier with nothing to do are the same
    series. `nora_dream_skipped_total{reason="schedule"}` is the difference."""
    metrics = Metrics()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), _KeyedLanguage(), metrics,
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
        schedule_clock=InMemoryDreamScheduleClock(),
    ), ticks=0.06)

    assert metrics.get("nora_dream_skipped_total", reason="schedule") > 0


async def test_two_adaptors_sharing_an_interval_can_hold_different_phases():
    """The fact tier's ordering. `fact-adjudication` and
    `fact-entity-resolution` share a daily interval over the SAME unit;
    what separates them is the phase, and the slot is what carries it.

    Same instant, same interval, offsets an hour apart — the two must land in
    slots that are independent, so one firing never consumes the other's.
    """
    sched_a = AdaptorSchedule(
        min_interval_seconds=86400, batch_limit=10, phase_offset_seconds=6 * 3600)
    sched_b = AdaptorSchedule(
        min_interval_seconds=86400, batch_limit=10, phase_offset_seconds=7 * 3600)

    # 2026-09-06 06:30 UTC — past A's 06:00 boundary, before B's 07:00 one.
    at = 1_788_675_000_000
    assert _slot(at, sched_a) != _slot(at - 3600_000, sched_a)
    assert _slot(at, sched_b) == _slot(at - 3600_000, sched_b)


async def test_a_never_fired_pair_is_not_read_as_epoch_zero():
    """`None` from the clock means «never fired» and must fire. A clock
    substituting 0 would also fire here, which is why the contract suite
    carries the distinction — this test pins only that absence fires at all."""
    clock = InMemoryDreamScheduleClock()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), _KeyedLanguage(), Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
        schedule_clock=clock,
    ), ticks=0.04)

    assert ("curator", "g1", 0) in runner.calls
    assert await clock.last_fired(adaptor_name="curator", key="g1") is not None


async def test_a_watermark_free_adaptor_with_no_clock_still_runs_every_tick():
    """The by-omission default, unchanged. A deployment that supplies
    schedules but no clock must not silently stop running its adaptors —
    the pre-schedule behaviour is the safe direction to fall back to."""
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), _KeyedLanguage(), Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
    ), ticks=0.05)

    assert [c for c in runner.calls if c[0] == "curator"].count(
        ("curator", "g1", 0)) > 1


async def test_the_watermark_is_held_when_the_run_reports_unavailable():
    """`ExtractionUnparseableError`'s guarantee, now a value instead of an
    exception. Advancing here consumes those messages permanently with nothing
    left to retry, and `nora_dream_evidence_lost_total` is structurally blind
    to it — it measures PRUNING."""
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner(unavailable_for="lexemes")

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1), _row(2)]}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes")], runtime=runner,
    ))

    assert runner.calls                      # it DID run
    assert [c for c in language.advance_calls if c[0] == "lexemes"] == []


def test_a_unit_with_no_failures_reads_its_whole_batch():
    """The control. Shrinking a healthy unit would quietly halve every
    adaptor's throughput for a problem it does not have."""
    assert _shrunk_batch_limit(80, 0) == 80


def test_each_consecutive_failure_halves_the_batch():
    """Issue #291: `unavailable` holds the watermark, so without this the
    retry re-reads the IDENTICAL range and a range-caused failure repeats
    forever. Measured: 80 messages emitted 2,124 completion tokens against a
    2,000 ceiling — 6% over, and 40 of them fit on the first retry."""
    assert _shrunk_batch_limit(80, 1) == 40
    assert _shrunk_batch_limit(80, 2) == 20
    assert _shrunk_batch_limit(80, 3) == 10


def test_the_shrink_stops_at_a_floor_rather_than_reaching_one_message():
    """A one-message batch is not a cheaper version of the same work — the
    objective reads a CONVERSATION, so a single message in isolation is a
    different question with a worse answer. The floor is also what stops a
    long outage (which also reports `unavailable`) from grinding every unit
    down to nothing while the provider is away."""
    assert _shrunk_batch_limit(80, 40) == 5
    assert _shrunk_batch_limit(80, 4) == 5
    # Never ABOVE the base: a small configured batch is not enlarged.
    assert _shrunk_batch_limit(3, 9) == 3


async def test_a_retry_reads_fewer_rows_than_the_attempt_that_failed():
    """The wiring, asserted on what the RUNNER was handed rather than on the
    helper — a pure function nothing calls is a phantom, and this is the call
    that was missing for 24 hours."""
    metrics = Metrics()
    runner = _RecordingRunner()
    rows = [_row(i) for i in range(1, 81)]

    await _dream_adaptor_once(
        adaptor=_StubAdaptor("note-candidates"), runtime=runner,
        record=_FakeRecord({"g1": rows}), language=_KeyedLanguage(lag=99),
        metrics=metrics, key="g1", safety_lag=0, quiet_seconds=0.0,
        max_age_seconds=1e9, batch_limit=80, failure_count=1,
    )

    assert runner.calls == [("note-candidates", "g1", 40)]
    assert metrics.get(
        "nora_dream_batch_shrunk_total", adaptor="note-candidates") == 1


async def test_a_first_attempt_reads_the_whole_batch_and_counts_no_shrink():
    """The positive control for the test above. A wiring that shrank
    unconditionally would pass it and halve every healthy unit's throughput,
    and `nora_dream_batch_shrunk_total` would fire on units that are fine —
    making the one series that says «something here does not fit» useless."""
    metrics = Metrics()
    runner = _RecordingRunner()
    rows = [_row(i) for i in range(1, 81)]

    await _dream_adaptor_once(
        adaptor=_StubAdaptor("note-candidates"), runtime=runner,
        record=_FakeRecord({"g1": rows}), language=_KeyedLanguage(lag=99),
        metrics=metrics, key="g1", safety_lag=0, quiet_seconds=0.0,
        max_age_seconds=1e9, batch_limit=80, failure_count=0,
    )

    assert runner.calls == [("note-candidates", "g1", 80)]
    assert metrics.get(
        "nora_dream_batch_shrunk_total", adaptor="note-candidates") == 0


async def test_the_watermark_advances_after_a_successful_run():
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1), _row(7)]}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes")], runtime=runner,
    ))

    assert ("lexemes", "g1", 7) in language.advance_calls


async def test_an_empty_batch_still_refreshes_the_age_clock_in_place():
    """The age-only condition is the ONE that fires at `lag == 0`, and without
    the refresh it is a latch with no reset: a dormant scope would fire once
    and then never stop, 288x/day, unattended."""
    language = _KeyedLanguage(age=1e9)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1.0, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes")], runtime=runner,
    ))

    assert ("lexemes", "g1", 0) in language.advance_calls


async def test_two_adaptors_hold_separate_cursors_for_one_scope():
    """The reason `consolidator_name` had to stop being the hardcoded
    `"language"`. Two adaptors sharing one row means the second never sees a
    message the first consumed."""
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(5)]}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes"), _StubAdaptor("exchanges")],
        runtime=runner,
    ))

    assert language.watermarks[("lexemes", "g1")] == 5
    assert language.watermarks[("exchanges", "g1")] == 5


# ---- gate D3: the isolation boundary, and the backoff key that moved with it


async def test_a_failing_adaptor_does_not_stop_its_sibling_running():
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner(raises_for="facts")

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1)]}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts"), _StubAdaptor("lexemes")], runtime=runner,
    ))

    assert ("lexemes", "g1", 1) in runner.calls


async def test_a_failing_adaptor_does_not_back_off_its_sibling():
    """The bug per-adaptor isolation exists to prevent, reintroduced one layer
    down: isolate the try/except per adaptor but leave the backoff keyed per
    SCOPE, and one failing adaptor silences every other adaptor in that scope.

    Measured by call COUNT across several ticks rather than by presence: the
    sibling appearing once proves it ran before any backoff could apply, which
    is exactly what a shared key would also produce."""
    language = _KeyedLanguage(age=1e9)
    runner = _RecordingRunner(raises_for="facts")

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts"), _StubAdaptor("lexemes")], runtime=runner,
    ), ticks=0.08)

    healthy = [c for c in runner.calls if c[0] == "lexemes"]
    failing = [c for c in runner.calls if c[0] == "facts"]
    assert len(healthy) > 1, "the healthy adaptor was backed off by its sibling"
    assert len(failing) == 1, "the failing adaptor was not backed off at all"


async def test_an_adaptor_still_in_flight_is_skipped_not_re_entered():
    """The per-unit lock. Re-entering would read the same un-advanced cursor
    and hand the same batch to a second model call."""
    language = _KeyedLanguage(age=1e9)
    metrics = Metrics()

    class _SlowRunner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, *, adaptor, key, batch):
            self.calls += 1
            await asyncio.sleep(5)
            return _StubOutcome()

    runner = _SlowRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, metrics,
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner,
    ), ticks=0.08)

    assert runner.calls == 1
    # `in_flight_adaptor`, not `in_flight`: one adaptor wedged and one scope
    # wedged are different diagnoses, and this is the one series that answers
    # «is the Dreamer stuck?».
    assert metrics.get(
        "nora_dream_skipped_total", reason="in_flight_adaptor") > 0
    assert metrics.get("nora_dream_skipped_total", reason="in_flight") == 0


# ---- the unit wall clock (issue #191): #60 bounded the adaptor's own
# inputs/commit; the cursor reads AROUND them were still bare awaits, and any
# one of them hanging reproduced #60 exactly — this unit never ticks again.


class _HangingRecord:
    """`_SinceReadable`'s own hang. `record.since` is one of the cursor reads
    `#60` left bare around the adaptor's own bounded `inputs`/`commit` (issue
    #191) — before `[dreamer] max_unit_hold_seconds` existed, nothing bounded
    this call at all.

    `blocked` is set the MOMENT `since` is entered, so a test can tell "it
    hung" from "it was never called" — the exit-code-5-shaped failure one
    layer up from a probe that measures nothing. `calls` counts every entry:
    the whole proof this file exists for is that the SECOND call happens at
    all.
    """

    def __init__(self) -> None:
        self.blocked = asyncio.Event()
        self.calls = 0
        self._forever = asyncio.Event()

    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        self.calls += 1
        self.blocked.set()
        await self._forever.wait()
        raise AssertionError("unreachable")  # pragma: no cover


async def test_a_hung_cursor_read_is_released_by_the_unit_wall_clock():
    """**Issue #191, end to end.** `record.since` hangs forever — the same
    shape a wedged connection to the audit store would produce, and one of
    the exact calls the issue names as still bare `await`s around the
    adaptor's own bounded `inputs`/`commit`.

    Before `[dreamer] max_unit_hold_seconds`, this reproduced #60 exactly:
    `_run_adaptor` never returns, `finally: running.discard(unit)` never
    runs, and `g1`'s `note-candidates` unit never ticks again — indistinguishable,
    on `nora_dream_skipped_total{reason="in_flight_adaptor"}` alone, from an
    ordinary long run.

    THREE properties proven at once, because a fix that only has one of them
    is not this fix: the unit is released WITHIN the wall clock (`since` is
    entered a SECOND time — the loop is not stuck), the dedicated metric
    counts the wedge on its own series, and the model is never reached (the
    hang is upstream of it, so a silent "success" is not possible here).
    """
    record = _HangingRecord()
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner()
    adaptor = _StubAdaptor("note-candidates")
    metrics = Metrics()

    async def _run() -> None:
        task = asyncio.create_task(dream_loop(
            record, language, metrics,
            scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=0,
            quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
            adaptors=[adaptor], runtime=runner,
            # Small and deliberate: this IS the value under test, not an
            # incidental speed-up. Several wall-clock cycles must fit inside
            # the window below for "ticks again" to be provable at all.
            max_unit_hold_seconds=0.05,
        ))
        await record.blocked.wait()      # the first attempt is wedged
        await asyncio.sleep(0.3)         # several wall-clock cycles elapse
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # A bounded TEST timeout (nora-prove-fix): reverting the fix must make
    # this fail or time out here, never hang the suite.
    await asyncio.wait_for(_run(), timeout=5)

    assert record.calls >= 2, (
        "record.since was entered only once — the unit never ticked again "
        "after the hang, issue #191's exact defect"
    )
    assert metrics.get(
        "nora_dream_unit_timeout_total", adaptor="note-candidates",
    ) >= 1
    assert not runner.calls, (
        "the model was reached even though the hang sits upstream of it"
    )


# ---- the additive guarantee, and the wiring mistake that is otherwise silent


async def test_adaptors_with_no_runtime_are_reported_rather_than_silently_idle(caplog):
    """A subsystem whose only symptom is silence. The loop would tick forever,
    write nothing, and be indistinguishable from a healthy quiet deployment."""
    with caplog.at_level(logging.ERROR):
        await _tick(dream_loop(
            _FakeRecord({}), _KeyedLanguage(), Metrics(),
            scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
            quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
            adaptors=[_StubAdaptor("facts")], runtime=None,
        ))

    assert "no runtime" in caplog.text


# ---- seeding: the trap that is cheap to forget and expensive to discover


async def test_a_newly_named_adaptor_is_seeded_from_the_shared_cursor():
    """`watermark_for` returns 0 for an unknown pair, so an unseeded adaptor
    re-reads the room from the beginning — safe, slow, expensive, and it looks
    exactly like a bug."""
    language = _KeyedLanguage()
    language.watermarks[("language", "g1")] = 412

    seeded = await seed_watermarks(
        language, [_StubAdaptor("lexemes")], keys=lambda a: ["g1"],
    )

    assert seeded == 1
    assert language.watermarks[("lexemes", "g1")] == 412


async def test_seeding_never_overwrites_a_cursor_that_already_exists():
    """`advance_watermark` is an UPSERT. Seeding unconditionally would clobber
    a live cursor — the disaster this function exists to avoid, not a stronger
    version of the fix."""
    language = _KeyedLanguage()
    language.watermarks[("language", "g1")] = 412
    language.watermarks[("lexemes", "g1")] = 900

    seeded = await seed_watermarks(
        language, [_StubAdaptor("lexemes")], keys=lambda a: ["g1"],
    )

    assert seeded == 0
    assert language.watermarks[("lexemes", "g1")] == 900


async def test_a_scope_the_SOURCE_NEVER_COVERED_stays_at_zero_by_default():
    """The historical behaviour, kept as the default and pinned so the new
    fallback cannot widen silently. An append-idempotent extractor may
    legitimately want the whole backlog."""
    language = _KeyedLanguage()
    language.heads["g1"] = 5000

    seeded = await seed_watermarks(
        language, [_StubAdaptor("lexemes")], keys=lambda a: ["g1"],
    )

    assert seeded == 0
    assert ("lexemes", "g1") not in language.watermarks


async def test_seed_at_head_starts_a_scope_the_source_never_covered_AT_THE_HEAD():
    """The defect this closes, measured live 2026-09-17: two Discord scopes had
    no row under the deleted `language` cursor, so both seeded at 0, and one
    held 1,916 messages with 1,616 unread. For an adaptor whose output is a
    single evolving HEAD that is worse than slow — every catch-up run installs
    an account derived from the room's PAST as the answer to what it is NOW."""
    language = _KeyedLanguage()
    language.heads["g1"] = 5000

    seeded = await seed_watermarks(
        language, [_StubAdaptor("culture")], keys=lambda a: ["g1"],
        schedules={"culture": AdaptorSchedule(
            min_interval_seconds=86400, batch_limit=300, seed_at_head=True,
        )},
    )

    assert seeded == 1
    assert language.watermarks[("culture", "g1")] == 5000


async def test_INHERITING_still_wins_over_the_head_when_there_is_something_to_inherit():
    """The head is a FALLBACK. A renamed adaptor must still inherit its
    predecessor's position, or it re-reads whatever happened in between."""
    language = _KeyedLanguage()
    language.watermarks[("language", "g1")] = 412
    language.heads["g1"] = 5000

    await seed_watermarks(
        language, [_StubAdaptor("culture")], keys=lambda a: ["g1"],
        schedules={"culture": AdaptorSchedule(
            min_interval_seconds=86400, batch_limit=300, seed_at_head=True,
        )},
    )

    assert language.watermarks[("culture", "g1")] == 412


async def test_a_scope_with_NO_MESSAGES_is_left_alone_even_with_seed_at_head():
    """0 is already the right cursor for an empty room, and writing a row for
    it would claim something was read."""
    language = _KeyedLanguage()

    seeded = await seed_watermarks(
        language, [_StubAdaptor("culture")], keys=lambda a: ["g1"],
        schedules={"culture": AdaptorSchedule(
            min_interval_seconds=86400, batch_limit=300, seed_at_head=True,
        )},
    )

    assert seeded == 0
    assert ("culture", "g1") not in language.watermarks


async def test_seeding_is_idempotent():
    language = _KeyedLanguage()
    language.watermarks[("language", "g1")] = 412
    adaptors = [_StubAdaptor("lexemes")]

    first = await seed_watermarks(
        language, adaptors, keys=lambda a: ["g1"])
    second = await seed_watermarks(
        language, adaptors, keys=lambda a: ["g1"])

    assert (first, second) == (1, 0)


async def test_seeding_skips_a_watermarkless_adaptor():
    """A cursor for an adaptor that never reads one is a row nothing maintains,
    and `watermark_age_seconds` over it would answer a question nobody asked."""
    language = _KeyedLanguage()
    language.watermarks[("language", "g1")] = 412

    seeded = await seed_watermarks(
        language, [_StubAdaptor("facts", uses_watermark=False)],
        keys=lambda a: ["g1"],
    )

    assert seeded == 0
    assert ("facts", "g1") not in language.watermarks


async def test_seeding_leaves_a_scope_the_source_has_never_read():
    """Nothing to inherit and nothing to skip. Seeding 0 over 0 would write a
    row whose only effect is to make `watermark_age_seconds` start ticking for
    a room the source has not reached yet."""
    language = _KeyedLanguage()

    seeded = await seed_watermarks(
        language, [_StubAdaptor("lexemes")], keys=lambda a: ["g1"],
    )

    assert seeded == 0
    assert language.advance_calls == []


async def test_an_unavailable_outcome_backs_off_even_though_nothing_raised():
    """The defect a review probe caught, and the reason it hid.

    `DreamerRuntime.run` catches everything the model call can throw — a 429
    after the retry policy is spent, a reset connection, its own wall clock —
    and returns `unavailable` NORMALLY. So `_run_adaptor`'s `except` never sees
    a provider outage, and the success path was running `failures.pop(unit)`,
    which did not merely fail to back off: it CLEARED backoff accrued from an
    unrelated failure.

    Measured before the fix: 29 model attempts in ~25 ticks against an
    always-unavailable runtime, versus 1 against one that raises. At the
    shipped 300s tick that is a 429 answered at full cadence forever — exactly
    what `dream_loop`'s own docstring says the backoff exists to prevent.

    **The two tests above could not catch it** because their runner RAISES, and
    the real runtime cannot. That is the shape worth remembering: a double that
    fails in a way the real collaborator never does tests a path production
    does not take."""
    language = _KeyedLanguage(age=1e9)
    runner = _RecordingRunner(unavailable_for="facts")

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner,
    ), ticks=0.1)

    assert len(runner.calls) == 1, (
        f"an unavailable run was retried {len(runner.calls)} times with no "
        "backoff — a provider outage answered at full cadence"
    )


async def test_a_nothing_found_run_is_a_success_and_does_not_back_off():
    """The mirror of the test above, so the fix cannot degrade into «treat
    every outcome as a failure».

    `nothing_found` means the adaptor LOOKED and there was nothing — R-27's
    middle state, and a perfectly healthy tick. Backing off on it would make a
    quiet room progressively invisible: the quieter it got, the less often
    anything would look at it."""
    language = _KeyedLanguage(age=1e9)
    runner = _RecordingRunner(status="nothing_found")

    await _tick(dream_loop(
        _FakeRecord({}), language, Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner,
    ), ticks=0.08)

    assert len(runner.calls) > 1, "a healthy nothing_found run was backed off"


async def test_the_dream_batch_starts_above_the_consolidation_watermark():
    """**The cursor, asserted rather than assumed.**

    `_dream_once` reads `after_id=watermark`, and nothing checked it: the fake
    store returned its whole batch whatever `after_id` was, so changing that
    line to `after_id=0` — every tick re-reading the conversation from the
    start and re-feeding rows already consolidated — left all 6142 tests green
    (2026-09-09). It is the only `.since(` call site in `nora/`, so no other
    test could have caught it.

    Five rows, watermark at 3: the Dreamer must be handed the two above it.
    """
    class _BacklogPastTheWatermark(_KeyedLanguage):
        """`_KeyedLanguage` reports `lag = 0` for any non-zero `after_id` — it
        models «watermark set means caught up», which is right for the tests it
        was written for and makes this one unfireable. Backlog is the premise
        here, so `lag` is constant."""

        async def lag(self, *, scope_id: str, after_id: int) -> int:
            return 1000

    language = _BacklogPastTheWatermark()
    language.watermarks[("note-curation", "g1")] = 3
    runner = _RecordingRunner()
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": [_row(i) for i in range(1, 6)]}), language,
        Metrics(), scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        instructions=_FakeInstructions({"note-curation": 1}),
        adaptors=[_StubAdaptor("note-curation")], runtime=runner,
    ))
    await asyncio.sleep(0.15)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert runner.calls, "the Dreamer never ran"
    sizes = [n for _adaptor, _key, n in runner.calls]
    assert sizes[0] == 2, (
        f"the first batch should be rows 4 and 5, the two above the watermark "
        f"at 3; got {sizes[0]}. A batch of 5 means the cursor was ignored."
    )
    # Later ticks legitimately see 0 — the fold advanced the watermark past
    # them. What must never reappear is the whole conversation.
    assert 5 not in sizes, (
        f"a batch of 5 came back after the watermark advanced ({sizes}) — "
        f"already-consolidated rows are being re-fed every tick"
    )
