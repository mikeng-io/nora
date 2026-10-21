"""`nora.analytics.__main__._tick`: the liveness gauge (`nora_analytics_rollup_
age_seconds` must climb while a subject is failing, not reset to a hardcoded
0.0 on every call — see `_publish_liveness` in `nora.presence.collector`, the
pattern this mirrors), the watermark it builds to, and that a failed run
never takes the daemon down. The per-subject isolation itself is
`RollupRunner.run`'s (`tests/unit/test_rollup_runner.py`)."""

from __future__ import annotations

from nora.analytics.__main__ import _tick
from nora.analytics.composition import AnalyticsDependencies
from nora.analytics.runner import RunReport, Subject
from nora.obs.metrics import Metrics


class _FakeRunner:
    """Just enough of `RollupRunner` for `_tick`: records the watermark each
    run was asked for and reports the chosen subjects as failed."""

    def __init__(self, *, fail: set[str], explode: bool = False) -> None:
        self.fail = fail
        self.explode = explode
        self.runs: list[int] = []

    async def ensure_build(self, *, through_ms: int) -> int:
        return 7

    async def run(self, *, build_id: int, through_ms: int) -> RunReport:
        if self.explode:
            raise RuntimeError("database gone")
        self.runs.append(through_ms)
        return RunReport(
            build_id=build_id, through_ms=through_ms, folds={},
            failed=tuple(sorted(self.fail)), units=(),
        )


def _deps(runner: _FakeRunner, metrics: Metrics, *names: str) -> AnalyticsDependencies:
    return AnalyticsDependencies(
        runner=runner,  # type: ignore[arg-type]
        persons=None,
        subjects=tuple(Subject(subject_id=n, person_ids=(n,), circle_ids=("c1",)) for n in names),
        metrics=metrics, metrics_port=0, tick_seconds=0.0, grace_seconds=60.0,
    )


async def test_a_tick_builds_to_the_wall_clock_less_the_grace(monkeypatch):
    from nora.analytics import __main__ as main_mod

    monkeypatch.setattr(main_mod.time, "time", lambda: 1_000_000.0)
    runner = _FakeRunner(fail=set())
    await _tick(_deps(runner, Metrics(), "ok"), {}, {})
    assert runner.runs == [(1_000_000 - 60) * 1000]


async def test_a_run_that_raises_does_not_take_the_daemon_down():
    metrics = Metrics()
    await _tick(_deps(_FakeRunner(fail=set(), explode=True), metrics, "a"), {}, {})
    assert metrics.get("nora_analytics_rollup_age_seconds") == 0.0


async def test_liveness_gauge_climbs_for_a_subject_that_keeps_failing(monkeypatch):
    # A gauge set to a hardcoded 0.0 after every call (success or not) can never
    # reveal "up but not deriving"; only a value computed fresh from a baseline
    # that ONLY advances on success can climb.
    from nora.analytics import __main__ as main_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(main_mod.time, "time", lambda: clock["t"])
    metrics = Metrics()
    deps = _deps(_FakeRunner(fail={"bad"}), metrics, "ok", "bad")
    last_success: dict[str, float] = {}
    first_seen: dict[str, float] = {}

    await main_mod._tick(deps, last_success, first_seen)
    assert metrics.get("nora_analytics_rollup_age_seconds") == 0.0

    clock["t"] += 300.0  # 5 minutes later, "bad" is STILL failing
    await main_mod._tick(deps, last_success, first_seen)
    assert metrics.get("nora_analytics_rollup_age_seconds") == 300.0
    assert metrics.get("nora_analytics_rollup_failures_total") == 2
    assert metrics.get("nora_analytics_subjects") == 2
