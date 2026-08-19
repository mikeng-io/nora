"""Task 10 review fix: `nora.analytics.__main__._tick`'s per-subject
isolation (spec §5 — one subject's failure neither aborts the sweep nor
crashes the daemon) and its liveness gauge (`nora_analytics_rollup_age_
seconds` must climb while a subject is failing, not reset to a hardcoded
0.0 on every call — see `_publish_liveness` in `nora.presence.collector`,
the pattern this mirrors)."""

from __future__ import annotations

from nora.analytics.__main__ import _tick
from nora.analytics.composition import AnalyticsDependencies
from nora.obs.metrics import Metrics


class _FakeRollup:
    """Just enough of `MobilityRollup`'s interface for `_tick` — duck-typed,
    not a `MobilityRollup` subclass, so a chosen subject's `run_once` can be
    made to raise on demand."""

    def __init__(self, *, fail_subjects: set[str]) -> None:
        self._fail_subjects = fail_subjects
        self.attempted: list[str] = []

    async def watermark(self, *, subject_id: str) -> tuple[int, int]:
        return (0, 0)

    async def run_once(
        self, *, subject_id: str, person_ids: list[str], circle_id: str, now_ms: int,
    ) -> None:
        self.attempted.append(subject_id)
        if subject_id in self._fail_subjects:
            raise RuntimeError(f"boom: {subject_id}")


def _deps(rollup: _FakeRollup, *, subjects: tuple, metrics: Metrics) -> AnalyticsDependencies:
    return AnalyticsDependencies(
        rollup=rollup, persons=None, subjects=subjects,  # type: ignore[arg-type]
        metrics=metrics, metrics_port=0, tick_seconds=0.0,
    )


async def test_one_subjects_failure_does_not_stop_the_others_or_the_loop():
    # The failing subject is listed FIRST — a naive implementation that lets
    # an exception propagate out of `_tick`'s loop would never reach "ok" at
    # all, and the `await _tick(...)` call below would itself raise and fail
    # this test.
    rollup = _FakeRollup(fail_subjects={"bad"})
    deps = _deps(
        rollup,
        subjects=(("bad", ["bad"], "c1"), ("ok", ["ok"], "c1")),
        metrics=Metrics(),
    )
    await _tick(deps, {}, {}, {})
    assert rollup.attempted == ["bad", "ok"]


async def test_a_failing_subjects_watermark_does_not_advance_a_second_tick_retries():
    rollup = _FakeRollup(fail_subjects={"bad"})
    deps = _deps(rollup, subjects=(("bad", ["bad"], "c1"),), metrics=Metrics())
    last_run: dict[str, float] = {}
    await _tick(deps, last_run, {}, {})
    await _tick(deps, last_run, {}, {})
    # tick_seconds=0.0 means every tick re-evaluates every subject
    # regardless of when it last ran, so a failing subject that never
    # advances its watermark is retried, not abandoned.
    assert rollup.attempted == ["bad", "bad"]


async def test_liveness_gauge_climbs_for_a_subject_that_keeps_failing(monkeypatch):
    # The review finding this pins: setting the gauge to a hardcoded 0.0
    # right after every call (success or not) can never reveal "up but not
    # deriving" — only a value computed fresh from a baseline that ONLY
    # advances on success can climb.
    from nora.analytics import __main__ as main_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(main_mod.time, "time", lambda: clock["t"])

    rollup = _FakeRollup(fail_subjects={"bad"})
    metrics = Metrics()
    deps = _deps(
        rollup,
        subjects=(("ok", ["ok"], "c1"), ("bad", ["bad"], "c1")),
        metrics=metrics,
    )
    last_run: dict[str, float] = {}
    last_success: dict[str, float] = {}
    first_seen: dict[str, float] = {}

    await main_mod._tick(deps, last_run, last_success, first_seen)
    assert metrics.get("nora_analytics_rollup_age_seconds", subject="ok") == 0.0
    assert metrics.get("nora_analytics_rollup_age_seconds", subject="bad") == 0.0

    clock["t"] += 300.0  # 5 minutes later, "bad" is STILL failing
    await main_mod._tick(deps, last_run, last_success, first_seen)

    # "ok" keeps succeeding every tick, so its age stays pinned at 0 —
    # freshly computed each time (baseline == this tick's own success), not
    # a value that merely happens to equal the old hardcoded constant.
    assert metrics.get("nora_analytics_rollup_age_seconds", subject="ok") == 0.0
    # "bad" has never succeeded: the gauge climbs from first-seen, exactly
    # the "up but not deriving" signal this metric exists to show.
    assert metrics.get("nora_analytics_rollup_age_seconds", subject="bad") == 300.0
    # And both subjects were attempted on both ticks — the failure never
    # stopped "ok" from running, nor did it stop "bad" from being retried.
    assert rollup.attempted == ["ok", "bad", "ok", "bad"]
