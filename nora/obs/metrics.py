"""In-process metrics recording (spec C.9), backed by prometheus-client.

Callers record through `inc`/`gauge`/`observe`; this module also exports
Prometheus text format directly via `render`.

Every `Metrics()` instance owns its own `CollectorRegistry` — collectors are
instance state, not a module global. That is what gives tests real isolation:
prometheus-client's `CollectorRegistry` has no `reset()`, so a fresh instance
per test (`tests/conftest.py`) replaces resetting a shared one.

`get`/`snapshot` do PARTIAL label matching: a query mentions only the labels
it cares about and the result sums over every dimension it left unspecified.
This is what lets `metrics.get("nora_errors_total", stage="ambient")` keep
working when `nora_errors_total` also carries a `platform` label that a call
site may not pass (in which case it is recorded under `platform=""`, never
dropped or rejected).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from .metric_specs import (
    _COUNTERS,
    _GAUGES,
    _HISTOGRAMS,
    DECLARED_NAMES,
)

__all__ = ["DECLARED_NAMES", "Metrics"]


def _obs_key(name: str, labels: dict[str, object]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))


def _full_labels(labelnames: tuple[str, ...], provided: dict[str, object]) -> dict[str, str]:
    """Every declared labelname gets a value. A caller that hasn't been
    passed the label (e.g. a background sweep with no platform of its own)
    degrades to `""` instead of crashing: prometheus-client requires a value
    for every declared label on every observation."""
    return {label: str(provided.get(label, "")) for label in labelnames}


def _series_matches(series_labels: tuple[tuple[str, str], ...], query: dict[str, object]) -> bool:
    values = dict(series_labels)
    return all(values.get(k) == str(v) for k, v in query.items())


class Metrics:
    """Records through prometheus-client collectors held on its own registry.

    Construct one per test, and exactly one for the whole process in
    production (`nora.app.composition.build_dependencies` builds it and
    threads it through `Dependencies`) — never a module-level singleton
    (DEC-13); there is nothing to reset, so a fresh instance per scope is used.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry if registry is not None else CollectorRegistry()
        self._counter_labels = {spec.name: spec.labelnames for spec in _COUNTERS}
        self._gauge_labels = {spec.name: spec.labelnames for spec in _GAUGES}
        self._counters = {
            spec.name: Counter(
                spec.name, spec.help_text, spec.labelnames, registry=self._registry,
            )
            for spec in _COUNTERS
        }
        self._gauges = {
            spec.name: Gauge(spec.name, spec.help_text, spec.labelnames, registry=self._registry)
            for spec in _GAUGES
        }
        self._histogram_labels = {spec.name: spec.labelnames for spec in _HISTOGRAMS}
        self._histograms = {
            spec.name: Histogram(
                spec.name, spec.help_text, spec.labelnames, buckets=spec.buckets,
                registry=self._registry,
            )
            for spec in _HISTOGRAMS
        }
        # Raw observations, kept so tests can read back exact recorded values
        # (p95 assertions, elapsed-time comparisons); prometheus-client's
        # Histogram only tracks bucket counts + sum. Never rendered by `render()`.
        self._observations: dict[tuple, list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if name in self._counters:
            counter = self._counters[name]
            labelnames = self._counter_labels[name]
            if labelnames:
                counter.labels(**_full_labels(labelnames, labels)).inc(value)
            else:
                counter.inc(value)
            return
        # honcho_feed_success/honcho_feed_failure are Gauges (see _GAUGES'
        # comment) but are incremented, never .set(); Gauge supports .inc().
        gauge = self._gauges[name]
        labelnames = self._gauge_labels[name]
        if labelnames:
            gauge.labels(**_full_labels(labelnames, labels)).inc(value)
        else:
            gauge.inc(value)

    def gauge(self, name: str, value: float, **labels: object) -> None:
        gauge = self._gauges[name]
        labelnames = self._gauge_labels[name]
        if labelnames:
            gauge.labels(**_full_labels(labelnames, labels)).set(value)
        else:
            gauge.set(value)

    def gauge_function(self, name: str, fn: Callable[[], float]) -> None:
        """Bind a gauge to a callback evaluated at SCRAPE time, not at call time.

        For gauges that must keep MOVING when the thing they measure has
        stopped. `gauge(name, value)` writes a number once, so a loop that
        publishes its own age would freeze that age when it dies. The callback
        runs inside `CollectorRegistry.collect()` (prometheus-client's
        `set_function`), so every scrape, `snapshot()`, `get()` and `render()`
        recomputes it.

        Unlabeled gauges only; a labeled gauge raises `ValueError`.
        """
        if self._gauge_labels[name]:
            raise ValueError(
                f"{name} declares labels {self._gauge_labels[name]}; "
                "gauge_function only supports unlabeled gauges"
            )
        self._gauges[name].set_function(fn)

    def observe(self, name: str, value: float, **labels: object) -> None:
        """Record one sample, under whatever labels the spec declares.

        `**labels` reach both the prometheus histogram and the test-readback
        dict. A histogram that declares no label takes the unlabelled path.
        """
        histogram = self._histograms[name]
        labelnames = self._histogram_labels[name]
        if labelnames:
            histogram.labels(**_full_labels(labelnames, labels)).observe(value)
        else:
            histogram.observe(value)
        self._observations[_obs_key(name, labels)].append(value)

    def observations(self, name: str, **labels: object) -> list[float]:
        """Every recorded sample whose labels match every kwarg given.

        PARTIAL matching, exactly as `get` does it: a caller that names no
        label sees every sample regardless of which labels the series carry.
        """
        matched: list[float] = []
        for (obs_name, obs_labels), values in self._observations.items():
            if obs_name == name and _series_matches(obs_labels, labels):
                matched.extend(values)
        return matched

    def snapshot(self) -> dict[tuple, float]:
        """Every counter/gauge sample, keyed `(name, sorted label pairs)`.

        Includes prometheus-client's own bookkeeping series (histogram
        buckets, the `_created` companions) under their own distinct names —
        callers filter by the exact name they want, as `nora.obs.health.
        _sum_series` and `get` below both do, so these never interfere.
        """
        result: dict[tuple, float] = {}
        for family in self._registry.collect():
            for sample in family.samples:
                result[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
        return result

    def get(self, name: str, **labels: object) -> float:
        """Sum every series named `name` whose labels match every kwarg given.

        Unspecified label dimensions are summed over, not required — see the
        module docstring for why.
        """
        total = 0.0
        for (series_name, series_labels), value in self.snapshot().items():
            if series_name == name and _series_matches(series_labels, labels):
                total += value
        return total

    def render(self) -> str:
        return generate_latest(self._registry).decode("utf-8")
