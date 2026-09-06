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

from nora.agent.dreamer import AdaptorSchedule
from nora.app.runtime import (
    BackgroundTasks,
    SupervisedAdapter,
    _backoff_elapsed,
    debounce_sweep_loop,
    dream_loop,
    prune_loop,
    seed_watermarks,
    start_adapters,
    supervise_adapter,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.language.consolidator import LanguageConsolidator
from nora.language.observer import LexemeObserver
from nora.obs.metrics import Metrics
from nora.tag import Debounce
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

        async def prune_all(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return 3

    store = FailFirstThenOkStore()
    metrics = Metrics()

    task = asyncio.create_task(prune_loop(store, metrics, interval_seconds=0.01))
    await asyncio.sleep(0.08)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert store.calls >= 2, "the loop must run again after a failing sweep"
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
    real_sweep = Debounce.sweep

    class ExplodingDebounce(Debounce):
        def sweep(self, older_than: float) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_sweep(self, older_than)

    d = ExplodingDebounce(1.0)
    metrics = Metrics()

    task = asyncio.create_task(
        debounce_sweep_loop(d, metrics, interval_seconds=0.01, ttl_seconds=5.0)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls["n"] >= 2
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
        return list(self._batches.get(conversation_id, []))[:limit]


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


async def test_a_second_tick_does_not_re_enter_a_running_scope():
    """Without the lock, a slow extraction lets the next tick re-fire against
    the same un-advanced watermark, and two cycles append the same evidence."""
    calls = 0
    started = asyncio.Event()

    class SlowConsolidator:
        name = "language"

        async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
            nonlocal calls
            calls += 1
            started.set()
            await asyncio.sleep(0.3)
            return 0

    record = _FakeRecord({"g1": [_row(1)]})
    language = _FakeLanguage(lag=1000)  # > safety_lag: fires every tick
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [SlowConsolidator()], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await started.wait()
    # Several more ticks fire in this window without the lock — the count
    # below is what proves it did not re-enter.
    await asyncio.sleep(0.15)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls == 1, "the second tick must not re-enter the still-running scope"
    assert metrics.get("nora_dream_skipped_total", reason="in_flight") >= 1


async def test_the_watermark_advances_only_after_observations_are_committed():
    """The unsafe direction is watermark-first: a crash between them loses that
    range forever, and nora_dream_evidence_lost_total is defined for the
    PRUNING case and is structurally blind to it."""
    order: list[str] = []

    class RecordingConsolidator:
        name = "language"

        async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
            order.append("consolidate")
            return len(batch)

    record = _FakeRecord({"g1": [_row(1), _row(2)]})
    language = _FakeLanguage(lag=1000, order=order)
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [RecordingConsolidator()], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert order == ["consolidate", "advance"], (
        "the watermark must advance AFTER the consolidator, never before"
    )
    assert language.advance_calls == [("g1", 2)], "advances to the batch's last id"

    # The crash-shaped half of the same proof: a consolidator that raises
    # must leave the watermark exactly where it was — reverting the `if
    # batch:`-guarded advance to run unconditionally, or moving it before the
    # consolidate loop, must turn this red. Scope "g2" gets its OWN
    # `_FakeRecord`, seeded with real rows — reusing the "g1"-only `record`
    # above would make `record.since(conversation_id="g2", ...)` return `[]`
    # unconditionally, so `advance_calls == []` would hold whether or not the
    # consolidator raised (the `if batch:` guard alone, not the crash path) —
    # exactly the decorative shape a reviewer already caught here once.
    order.clear()

    class FailingConsolidator:
        name = "language"

        async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
            order.append("consolidate")
            raise RuntimeError("model provider down")

    failing_record = _FakeRecord({"g2": [_row(1), _row(2)]})
    failing_language = _FakeLanguage(lag=1000, order=order)
    task2 = asyncio.create_task(dream_loop(
        failing_record, failing_language, [FailingConsolidator()], metrics,
        scopes=lambda: ["g2"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.05)
    task2.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task2

    assert order == ["consolidate"], "a failed cycle must never reach advance_watermark"
    assert failing_language.advance_calls == [], (
        "a raising consolidator, fed a REAL non-empty batch, must leave the "
        "watermark untouched — this only proves something if the batch was "
        "genuinely non-empty going in"
    )
    assert metrics.get("nora_errors_total", stage="dream") >= 1


# ---------------------------------------------------------------------------
# dream_loop portrait trigger (Dreamer Plan 3, 2026-08-20 room-portrait design)
# ---------------------------------------------------------------------------


class _CountingConsolidator:
    """Returns a FIXED `appended` count every cycle — the portrait trigger
    (`sum(appended) > 0`) is what these tests exercise, not consolidator
    behaviour itself."""

    name = "language"

    def __init__(self, appended: int) -> None:
        self._appended = appended
        self.consolidations = 0

    async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
        self.consolidations += 1
        return self._appended


class _RecordingPortrait:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[str] = []
        self._raises = raises

    async def synthesize(self, *, scope_id: str) -> None:
        self.calls.append(scope_id)
        if self._raises:
            raise RuntimeError("synthesis backend down")


async def test_portrait_is_not_called_when_nothing_was_appended():
    record = _FakeRecord({"g1": [_row(1)]})
    language = _FakeLanguage(lag=1000)  # fires every tick
    portrait = _RecordingPortrait()
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [_CountingConsolidator(appended=0)], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        portrait=portrait,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert portrait.calls == [], (
        "a tick where every consolidator appended zero must not trigger "
        "synthesis — the raw-message lag/quiet/age proxy fired, but the "
        "REAL property (did Plan 1/2 learn something) did not"
    )


async def test_portrait_is_called_when_a_consolidator_appended_something():
    record = _FakeRecord({"g1": [_row(1)]})
    language = _FakeLanguage(lag=1000)
    portrait = _RecordingPortrait()
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [_CountingConsolidator(appended=1)], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        portrait=portrait,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert portrait.calls == ["g1"], (
        "exactly one synthesis for the one tick that appended real material "
        "(the fake language's lag drops to 0 once the watermark advances, "
        "so later ticks in this window do not fire again)"
    )


async def test_portrait_is_called_when_only_one_of_several_consolidators_appends():
    """Sums ACROSS consolidators — a room where Plan 1 found nothing but
    Plan 2 found a pattern still has new material worth narrating."""
    record = _FakeRecord({"g1": [_row(1)]})
    language = _FakeLanguage(lag=1000)
    portrait = _RecordingPortrait()
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language,
        [_CountingConsolidator(appended=0), _CountingConsolidator(appended=1)],
        metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        portrait=portrait,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert portrait.calls, "one appending consolidator among several must still trigger synthesis"


async def test_a_portrait_failure_does_not_raise_or_block_the_watermark_advance():
    """Independent concerns: a portrait synthesis failure must not look like
    a Plan 1/2 failure to `_run_one` (which would trip THEIR backoff), and
    must not have blocked the watermark advance that already happened before
    this step runs."""
    record = _FakeRecord({"g1": [_row(1), _row(2)]})
    language = _FakeLanguage(lag=1000)
    portrait = _RecordingPortrait(raises=True)
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [_CountingConsolidator(appended=1)], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        portrait=portrait,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert portrait.calls, "synthesis must have been attempted"
    assert language.advance_calls, "the watermark advance must not be blocked by a portrait failure"
    assert metrics.get("nora_errors_total", stage="portrait") >= 1
    assert metrics.get("nora_errors_total", stage="dream") == 0, (
        "a portrait failure must not be counted as a Plan 1/2 dream-cycle failure"
    )


async def test_dream_loop_with_no_portrait_collaborator_behaves_exactly_as_before():
    """`portrait=None` (the default) — every EXISTING call site in this file
    omits the keyword entirely; this proves that omission still works."""
    record = _FakeRecord({"g1": [_row(1)]})
    language = _FakeLanguage(lag=1000)
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [_CountingConsolidator(appended=1)], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert language.advance_calls, "the loop must still tick and advance normally"


class _FakeModel:
    """The `LexemeObserver` model collaborator for the two tests below —
    same shape `tests/unit/test_language_observer.py::FakeModel` uses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.model = "fake-runtime-model"

    async def complete(self, *, prompt: str) -> str:
        return self._responses.pop(0)


class _FakeConsolidatorStore:
    """The write-side surface `LanguageConsolidator` needs
    (`append_observations`/`rescore`) — separate from `_FakeLanguage` above,
    which is `dream_loop`'s own CURSOR surface. Production wires both onto
    the SAME `SqlLanguageStore` instance; this test keeps them apart on
    purpose, exactly like `_FakeLanguage`/`RecordingConsolidator` already do,
    so nothing here depends on the two being one object."""

    def __init__(self) -> None:
        self.rescore_calls: list[str] = []

    async def append_observations(self, *, scope_id, observations) -> int:
        return len(observations)

    async def rescore(self, *, scope_id, now_ms) -> None:
        self.rescore_calls.append(scope_id)


async def test_an_unparseable_model_response_leaves_the_watermark_unmoved():
    """Finding 2 (final whole-branch review), proven at the level that
    actually matters: through a REAL `LanguageConsolidator` wrapping a REAL
    `LexemeObserver` fed the exact prose a degraded model returns, run
    through `dream_loop` itself — not a hand-rolled fake `RoomConsolidator`
    standing in for the claim. Before the fix, `LexemeObserver.observe`
    returned `[]` for prose exactly as it does for a genuine empty
    extraction, `consolidate` returned `0` either way, and `_dream_once`'s
    `if batch:` guard advanced the watermark over messages the model never
    actually read — consuming them forever. Reverting
    `LanguageConsolidator.consolidate`'s `if not result.parsed: raise
    ExtractionUnparseableError(...)` guard must turn this red."""
    record = _FakeRecord({"g1": [_row(1), _row(2)]})
    language = _FakeLanguage(lag=1000)
    store = _FakeConsolidatorStore()
    metrics = Metrics()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(
            _FakeModel(responses=["sorry, can't help with that"]), metrics=metrics,
            objective=OBJECTIVES,
        ),
        store=store,
    )

    task = asyncio.create_task(dream_loop(
        record, language, [consolidator], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert language.advance_calls == [], (
        "an unparseable batch, fed a REAL non-empty batch, must leave the "
        "watermark untouched"
    )
    assert store.rescore_calls == [], "an untrusted cycle must not rescore either"
    assert metrics.get("nora_errors_total", stage="dream") >= 1
    assert metrics.get("nora_language_parse_failures_total") >= 1


async def test_a_batch_that_parses_to_zero_candidates_still_advances():
    """The sibling proof: a WELL-FORMED, genuinely empty extraction
    (`{"observations": []}`) is not the same case as the test above and must
    not be treated like it — `_dream_once` must still advance the watermark,
    or a room that genuinely has nothing new would never drain its
    backlog."""
    record = _FakeRecord({"g1": [_row(1), _row(2)]})
    language = _FakeLanguage(lag=1000)
    store = _FakeConsolidatorStore()
    consolidator = LanguageConsolidator(
        observer=LexemeObserver(
            _FakeModel(responses=['{"observations": []}']),
            objective=OBJECTIVES,
        ),
        store=store,
    )
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [consolidator], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert language.advance_calls == [("g1", 2)], (
        "a batch that genuinely parsed to nothing new must still advance"
    )
    assert store.rescore_calls == ["g1"]


async def test_a_failing_extraction_does_not_starve_the_other_scopes():
    """`prune_loop`'s single try/except wraps ONE unit of work. A dream tick's
    unit is a LIST of scopes, so copying that shape verbatim lets one bad scope
    take out every other scope that tick."""
    good_calls = 0

    class BadConsolidator:
        name = "language"

        async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
            if scope_id == "bad":
                raise RuntimeError("boom")
            nonlocal good_calls
            good_calls += 1
            return len(batch)

    record = _FakeRecord({"bad": [_row(1)], "good": [_row(1)]})
    language = _FakeLanguage(lag=1000)
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [BadConsolidator()], metrics,
        scopes=lambda: ["bad", "good"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.07)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert good_calls >= 1, "the healthy scope must keep being consolidated"
    assert ("good", 1) in language.advance_calls
    assert metrics.get("nora_errors_total", stage="dream") >= 1


async def test_the_daily_firing_rescores_even_with_an_empty_batch():
    """Two of three fire conditions need lag > 0, so a dormant room would never
    decay — its lexicon would stay `live` describing a slang era that ended."""
    batches: list[list[TranscriptRow]] = []

    class RecordingConsolidator:
        name = "language"

        async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
            batches.append(list(batch))
            return 0

    record = _FakeRecord({"g1": []})
    # lag == 0 and quiet is irrelevant to that branch; only age crosses the
    # threshold — the ONE fire condition that does not require lag > 0.
    language = _FakeLanguage(lag=0, quiet=0.0, age=999_999)
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [RecordingConsolidator()], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=100, batch_limit=10,
    ))
    await asyncio.sleep(0.15)  # several ticks' worth
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert batches, "the 24h/age firing must still reach consolidate with an empty batch"
    assert all(b == [] for b in batches)
    assert len(batches) == 1, (
        "a dormant scope must fire the age condition ONCE across this whole "
        "window, not on every subsequent tick. `watermark_age_seconds` reads "
        "`updated_at`, which ONLY `advance_watermark` writes — an empty "
        "batch that never refreshes it leaves `age` pinned at its old value "
        "forever, so a background job meant to run once a day instead reruns "
        "on every tick (288x/day at the shipped 300s cadence)."
    )
    assert language.advance_calls == [("g1", 0)], (
        "the empty-batch fire must still refresh the watermark row, at the "
        "SAME last_message_id (0 here — nothing was ever consolidated) — "
        "this resets the age clock without moving the cursor forward"
    )
    # An INDEPENDENT witness that several ticks actually elapsed during the
    # sleep above, not just one. `len(batches) == 1` alone passes VACUOUSLY
    # on a heavily stalled machine where only a single tick fires in the
    # window — green for the wrong reason, and the latch this test exists to
    # catch could be back with nobody noticing. `watermark_for` is called
    # FIRST and UNCONDITIONALLY by `_dream_once` on every tick a scope is
    # considered, whether or not it goes on to fire — so its count is a real
    # measure of ticks elapsed, independent of `batches`.
    assert language.watermark_for_calls > 1, (
        "fewer than two ticks were observed — lengthen the sleep above; "
        "`len(batches) == 1` alone cannot tell a genuine single firing from "
        "a stalled test that only ran once"
    )


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


async def test_a_failed_scope_is_not_retried_before_its_backoff_elapses():
    """Non-negotiable 4: the backoff comes from `supervise_adapter`'s shape,
    not from `prune_loop`'s unconditional-retry-every-tick one. Deleting the
    `_backoff_elapsed` check in `dream_loop` entirely leaves every OTHER test
    in this file green — this is the one that goes red without it: with no
    backoff, a failed scope would be retried on every subsequent tick, and
    `attempts` would climb well past 1 inside this window.

    Uses the REAL (unpatched) default backoff constants — no need to shrink
    them: `_DREAM_BACKOFF_BASE_SECONDS` (60s) is enormously longer than the
    ~0.15s this test observes, so a single retry inside that window can only
    happen if the backoff check is missing or broken, never from ordinary
    timing noise.
    """
    attempts = 0

    class AlwaysFailingConsolidator:
        name = "language"

        async def consolidate(self, *, scope_id: str, batch: list[TranscriptRow]) -> int:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("model provider down")

    record = _FakeRecord({"g1": [_row(1)]})
    language = _FakeLanguage(lag=1000)
    metrics = Metrics()

    task = asyncio.create_task(dream_loop(
        record, language, [AlwaysFailingConsolidator()], metrics,
        scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
    ))
    await asyncio.sleep(0.15)  # 7+ ticks, all well inside the 60s backoff base
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert attempts == 1, (
        "a failed scope must not be retried before its backoff window "
        "elapses — every tick after the first failure must be a no-op skip, "
        "not a retry"
    )
    assert metrics.get("nora_errors_total", stage="dream") == 1


async def test_the_dream_loop_says_it_started(caplog):
    """The Dreamer was the ONE background loop with no positive liveness signal.

    Every sibling announces itself (`heartbeat started: tick every 60.0s`,
    `nora/app/heartbeat.py`), and `__main__` logs only the DISABLED branch — so
    with `language.enabled = true` the logs said nothing at all, and «is it
    running?» could only be answered from a MISSING line. That is an absence
    argument about a subsystem that renders into every tag's prefill.

    It logs from inside the loop, not from the spawn site, so it reports that
    the loop is TICKING rather than that a branch was taken. The scope count is
    in it because zero scopes is the quiet failure this cannot otherwise show:
    a loop that runs forever over nothing looks identical to a healthy one.
    """
    record = _FakeRecord([])
    language = _FakeLanguage(lag=1000)

    with caplog.at_level(logging.INFO, logger="nora.app.runtime"):
        task = asyncio.create_task(dream_loop(
            record, language, [], Metrics(),
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
    instructions = _FakeInstructions({"dreamer": 1, "room-language": 3})
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": []}), _FakeLanguage(lag=0), [], Metrics(),
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
    assert "'room-language': 3" in lines[0]


async def test_a_broken_instruction_corpus_cannot_take_the_clock_down(caplog):
    """The line reads a HOT-RELOADING corpus off a bind mount, and it runs
    OUTSIDE `dream_loop`'s per-scope try/except — so an uncaught exception here
    would stop the Dreamer's clock for the life of the process, in the name of
    a log line. Warned rather than swallowed: an operator has to be able to see
    that the corpus stopped being readable."""
    instructions = _FakeInstructions(fail=True)
    consolidator = _CountingConsolidator(appended=1)
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": [_row(1)]}), _FakeLanguage(lag=1000), [consolidator],
        Metrics(), scopes=lambda: ["g1"], tick_seconds=0.02, safety_lag=10,
        quiet_seconds=999_999, max_age_seconds=999_999, batch_limit=10,
        instructions=instructions,
    ))
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.15)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert instructions.calls > 1, "the loop kept ticking after the failure"
    assert consolidator.consolidations > 0, "the Dreamer kept working"
    assert any("instruction versions unavailable" in r.getMessage() for r in caplog.records)


async def test_no_instruction_source_logs_no_line_and_changes_nothing(caplog):
    """`instructions` is optional so every existing caller and test keeps
    working unchanged; absent, the loop is byte-for-byte what it was."""
    task = asyncio.create_task(dream_loop(
        _FakeRecord({"g1": []}), _FakeLanguage(lag=0), [], Metrics(),
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
        self.objective = "room-language"
        self.envelope_key = "observations"
        self.tools: frozenset[str] = frozenset()

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


async def test_an_adaptor_is_ticked_over_the_scopes_when_it_is_scope_keyed():
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1)], "g2": [_row(2)]}), language, [], Metrics(),
        scopes=lambda: ["g1", "g2"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes")], runtime=runner,
    ))

    assert {(a, k) for a, k, _ in runner.calls} == {("lexemes", "g1"), ("lexemes", "g2")}


async def test_a_workspace_keyed_adaptor_is_ticked_once_per_workspace():
    """Not a wider or narrower batch — a DIFFERENT key. Two rooms of one
    workspace must produce ONE tick, or the fact tier adjudicates the same
    claim once per room and calls it corroboration."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, [], Metrics(),
        scopes=lambda: ["g1", "g2"], workspaces=lambda: ["nora"],
        tick_seconds=0.01, safety_lag=0, quiet_seconds=0.0,
        max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts", key_kind="workspace", uses_watermark=False)],
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
            _FakeRecord({}), _KeyedLanguage(), [], Metrics(),
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
        _FakeRecord({"g1": [_row(1)]}), language, [], Metrics(),
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
        _FakeRecord({}), language, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
    ), ticks=0.08)

    assert [c for c in runner.calls if c[0] == "curator"] == [("curator", "g1", 0)]


async def test_a_scheduled_watermark_free_adaptor_fires_again_after_the_interval():
    """The other half of the clock: the interval elapses and the adaptor
    fires again — bounded above by the tick count (the interval is holding)
    and below by more than one (the first fire was not a latch)."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=0.03, batch_limit=10)},
    ), ticks=0.12)

    calls = [c for c in runner.calls if c[0] == "curator"]
    assert 2 <= len(calls) < 12


async def test_a_watermark_free_adaptor_without_a_schedule_still_runs_every_tick():
    """The invariant the schedule path must not disturb: NO entry in the
    mapping is every-tick, exactly as before schedules existed."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, [], Metrics(),
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
        _FakeRecord({"g1": [_row(1)]}), young, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner, schedules=schedules,
    ))
    assert [c for c in runner.calls if c[0] == "facts"] == []

    # An OLD row but no backlog: the daily batch is data-gated, so no fire.
    quiet_room = _KeyedLanguage(lag=0, age=1e9)
    await _tick(dream_loop(
        _FakeRecord({}), quiet_room, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner, schedules=schedules,
    ))
    assert [c for c in runner.calls if c[0] == "facts"] == []

    # Both: fires, and on the schedule's own batch limit.
    due = _KeyedLanguage(lag=99, age=1e9)
    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1), _row(2)]}), due, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner, schedules=schedules,
    ))
    assert ("facts", "g1", 2) in runner.calls


async def test_a_scheduled_watermark_free_adaptor_tracks_last_fired_per_key():
    """`last_fired_at` is keyed `f"{adaptor.name}:{key}"` (33 §5 N1a) — one
    workspace's curator firing must never hold back or advance another's.
    Both scopes fire on the first tick (nothing has fired yet for either
    key), and neither refires inside the interval."""
    language = _KeyedLanguage()
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({}), language, [], Metrics(),
        scopes=lambda: ["g1", "g2"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("curator", uses_watermark=False)],
        runtime=runner,
        schedules={"curator": AdaptorSchedule(
            min_interval_seconds=1e9, batch_limit=10)},
    ), ticks=0.08)

    calls = [c for c in runner.calls if c[0] == "curator"]
    assert calls.count(("curator", "g1", 0)) == 1
    assert calls.count(("curator", "g2", 0)) == 1


async def test_the_watermark_is_held_when_the_run_reports_unavailable():
    """`ExtractionUnparseableError`'s guarantee, now a value instead of an
    exception. Advancing here consumes those messages permanently with nothing
    left to retry, and `nora_dream_evidence_lost_total` is structurally blind
    to it — it measures PRUNING."""
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner(unavailable_for="lexemes")

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1), _row(2)]}), language, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
        adaptors=[_StubAdaptor("lexemes")], runtime=runner,
    ))

    assert runner.calls                      # it DID run
    assert [c for c in language.advance_calls if c[0] == "lexemes"] == []


async def test_the_watermark_advances_after_a_successful_run():
    language = _KeyedLanguage(lag=99)
    runner = _RecordingRunner()

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(1), _row(7)]}), language, [], Metrics(),
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
        _FakeRecord({}), language, [], Metrics(),
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
        _FakeRecord({"g1": [_row(5)]}), language, [], Metrics(),
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
        _FakeRecord({"g1": [_row(1)]}), language, [], Metrics(),
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
        _FakeRecord({}), language, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts"), _StubAdaptor("lexemes")], runtime=runner,
    ), ticks=0.08)

    healthy = [c for c in runner.calls if c[0] == "lexemes"]
    failing = [c for c in runner.calls if c[0] == "facts"]
    assert len(healthy) > 1, "the healthy adaptor was backed off by its sibling"
    assert len(failing) == 1, "the failing adaptor was not backed off at all"


async def test_a_failing_adaptor_does_not_disturb_the_consolidator_path():
    """The legacy schedule keys on a bare `scope_id` and the adaptor schedule on
    `adaptor:key`; a failing adaptor must not increment the scope's own
    consecutive-failure count."""
    language = _KeyedLanguage(age=1e9)
    runner = _RecordingRunner(raises_for="facts")
    consolidator = _CountingConsolidator(appended=0)

    await _tick(dream_loop(
        _FakeRecord({}), language, [consolidator], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner,
    ), ticks=0.08)

    assert consolidator.consolidations > 1


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
        _FakeRecord({}), language, [], metrics,
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


# ---- the additive guarantee, and the wiring mistake that is otherwise silent


async def test_no_adaptors_leaves_the_loop_exactly_as_it_was():
    """What makes the migration a per-adaptor cutover rather than a big-bang
    switch: the consolidator path still runs, untouched, with no runtime wired
    at all."""
    language = _FakeLanguage(lag=99)
    consolidator = _CountingConsolidator(appended=1)

    await _tick(dream_loop(
        _FakeRecord({"g1": [_row(3)]}), language, [consolidator], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=1e9, batch_limit=10,
    ))

    assert consolidator.consolidations >= 1
    assert ("g1", 3) in language.advance_calls


async def test_adaptors_with_no_runtime_are_reported_rather_than_silently_idle(caplog):
    """A subsystem whose only symptom is silence. The loop would tick forever,
    write nothing, and be indistinguishable from a healthy quiet deployment."""
    with caplog.at_level(logging.ERROR):
        await _tick(dream_loop(
            _FakeRecord({}), _KeyedLanguage(), [], Metrics(),
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
        _FakeRecord({}), language, [], Metrics(),
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
        _FakeRecord({}), language, [], Metrics(),
        scopes=lambda: ["g1"], tick_seconds=0.01, safety_lag=0,
        quiet_seconds=0.0, max_age_seconds=0.0, batch_limit=10,
        adaptors=[_StubAdaptor("facts")], runtime=runner,
    ), ticks=0.08)

    assert len(runner.calls) > 1, "a healthy nothing_found run was backed off"
