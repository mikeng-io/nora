"""In-process metrics recording (spec C.9), backed by prometheus-client.

M4/M5 record through this interface; this module also exports Prometheus
text format directly, with no call-site changes to `inc`/`gauge`/`observe`
(spec §9, audit fix #11).

Every `Metrics()` instance owns its own `CollectorRegistry` — collectors are
instance state, not a module global. That is what gives tests real isolation:
prometheus-client's `CollectorRegistry` has no `reset()`, so a fresh instance
per test (`tests/conftest.py`) replaces resetting a shared one.

`get`/`snapshot` do PARTIAL label matching: a query mentions only the labels
it cares about and the result sums over every dimension it left unspecified.
That is a deliberate widening from the old hand-rolled store's exact-tuple
lookup — it is what lets `metrics.get("nora_errors_total", stage="ambient")`
keep working now that `nora_errors_total` also carries a `platform` label
that an existing call site may not yet pass (in which case it is recorded
under `platform=""`, never dropped or rejected).
"""

from __future__ import annotations

from collections import defaultdict
from typing import NamedTuple

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

_DEFAULT_BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30)


class _CounterSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


class _GaugeSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


class _HistogramSpec(NamedTuple):
    name: str
    help_text: str
    buckets: tuple[float, ...] = _DEFAULT_BUCKETS


# Every metric name below already existed before this module's prometheus-client
# rewrite — names are append-only (an SRE's repo; renames break dashboards).
# `platform` is new on the four series spec §6 calls out (today only
# `nora_messages_total` had one, so with N adapters a stuck/failing platform
# was invisible in the other three): nora_tags_total, nora_replies_total,
# nora_silent_failures_total, nora_errors_total.
_COUNTERS: tuple[_CounterSpec, ...] = (
    _CounterSpec("nora_messages_total", "Ambient messages ingested, by platform.", ("platform",)),
    _CounterSpec(
        "nora_tags_total", "Tags detected and accepted for handling.", ("platform",),
    ),
    _CounterSpec("nora_tags_debounced_total", "Tags dropped by the debounce window."),
    _CounterSpec(
        "nora_tags_dropped_total", "Tags dropped for a reason other than debounce.", ("reason",),
    ),
    _CounterSpec(
        "nora_replies_total", "Delivery attempts, by outcome status.", ("status", "platform"),
    ),
    _CounterSpec(
        "nora_errors_total", "Internal errors, by processing stage.", ("stage", "platform"),
    ),
    _CounterSpec(
        "nora_silent_failures_total",
        "Failures kept silent from the group, by reason.",
        ("reason", "platform"),
    ),
    _CounterSpec("nora_guard_vetoes_total", "ReplyGuard vetoes, by rule.", ("rule",)),
    _CounterSpec(
        "nora_leak_blocks_total", "Replies blocked by the persona/canary leak guard.",
    ),
    _CounterSpec("nora_persona_reload_failures_total", "Persona hot-reload failures."),
    _CounterSpec("nora_store_busy_total", "SQLITE_BUSY responses from the audit store."),
    # An unmapped sender is an EXPECTED outcome (D6), so it is not an error —
    # but it was previously unobservable, and its symptom set is empty: the
    # message is still audited, the gate still runs, the prefill still shows
    # the sender's platform display name, and nothing reaches Honcho. Nora
    # looks like she is working and remembers nothing. Counting it is what
    # separates "nobody talked to her" from "her identity file is wrong, or
    # you edited it and never restarted" (mapping is startup-only, Q4).
    _CounterSpec(
        "nora_unmapped_senders_total",
        "Ambient messages whose sender resolved to no person.",
        ("platform",),
    ),
    # Deny-by-default has to be visible. "She stopped answering in that group"
    # must be answerable without a debugger: a denied ROOM is refused before
    # the audit write (nothing is recorded), a denied SENDER is refused only
    # at the point of answering (the message IS recorded, which is what
    # `nora persons pending` reads).
    _CounterSpec(
        "nora_denied_conversations_total",
        "Ambient messages from a conversation absent from policy.toml.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_denied_senders_total",
        "Tags from a sender not allowed in that conversation.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_adapter_restarts_total",
        "Adapter listen() loop restarts after a failure, by platform.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_tool_scope_violations_total",
        "A model-supplied tool argument disagreed with the scope the tool was "
        "bound to (e.g. read_buffer asked for a conversation_id other than the "
        "tagged one) and was ignored rather than honoured, by tool.",
        ("tool",),
    ),
)

_GAUGES: tuple[_GaugeSpec, ...] = (
    _GaugeSpec("nora_last_event_age_seconds", "Seconds since the last ingest event."),
    # `Gauge`, not `Counter`, on purpose: prometheus-client's exposition format
    # unconditionally appends `_total` to every counter-typed series' rendered
    # name (`exposition.py::generate_latest`, hardcoded, not configurable —
    # verified directly, including via a hand-rolled custom Collector, which
    # hits the same unconditional munging). These two names predate that
    # OpenMetrics convention and don't end in `_total`; using `Counter` would
    # silently rename them to `honcho_feed_success_total` /
    # `honcho_feed_failure_total` — exactly the kind of rename this repo's
    # append-only metric-name contract forbids. A `Gauge` only ever
    # incremented (see `Metrics.inc`) renders under its exact declared name
    # and is otherwise indistinguishable from a counter for these call sites.
    _GaugeSpec("honcho_feed_success", "Successful Honcho feed calls."),
    _GaugeSpec("honcho_feed_failure", "Failed Honcho feed calls."),
)

_HISTOGRAMS: tuple[_HistogramSpec, ...] = (
    _HistogramSpec(
        "nora_tag_latency_seconds", "End-to-end tag latency, including the model call.",
    ),
    _HistogramSpec("nora_llm_latency_seconds", "Model invocation latency."),
    _HistogramSpec("nora_overhead_seconds", "Tag latency excluding the model call."),
)

# The full set of declared metric names, independent of kind — what the
# name-preservation test (tests/test_metrics_export.py) enumerates against.
DECLARED_NAMES: frozenset[str] = (
    frozenset(spec.name for spec in _COUNTERS)
    | frozenset(spec.name for spec in _GAUGES)
    | frozenset(spec.name for spec in _HISTOGRAMS)
)


def _obs_key(name: str, labels: dict[str, object]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))


def _full_labels(labelnames: tuple[str, ...], provided: dict[str, object]) -> dict[str, str]:
    """Every declared labelname gets a value. A caller that hasn't been
    migrated to pass the new label yet (or never will — a background sweep
    with no platform of its own) degrades to `""` instead of crashing:
    prometheus-client requires a value for every declared label on every
    observation, unlike the old dynamic dict-keyed store."""
    return {label: str(provided.get(label, "")) for label in labelnames}


def _series_matches(series_labels: tuple[tuple[str, str], ...], query: dict[str, object]) -> bool:
    values = dict(series_labels)
    return all(values.get(k) == str(v) for k, v in query.items())


class Metrics:
    """Records through prometheus-client collectors held on its own registry.

    Construct one per test, and exactly one for the whole process in
    production (`nora.app.composition.build_dependencies` builds it and
    threads it through `Dependencies`) — never a module-level singleton
    (DEC-13); there is nothing to reset, so a fresh instance per scope is
    strictly simpler than resetting a shared one.
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
        self._histograms = {
            spec.name: Histogram(
                spec.name, spec.help_text, buckets=spec.buckets, registry=self._registry,
            )
            for spec in _HISTOGRAMS
        }
        # Raw observations, kept purely so tests can read back exact recorded
        # values (p95 assertions, elapsed-time comparisons) — prometheus-client's
        # Histogram only tracks bucket counts + sum, not individual samples, and
        # this is never rendered by `render()`.
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
        # comment) but are still incremented, never .set() — Gauge supports
        # .inc() natively for exactly this "count-only, legacy name" case.
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

    def observe(self, name: str, value: float, **labels: object) -> None:
        self._histograms[name].observe(value)
        self._observations[_obs_key(name, labels)].append(value)

    def observations(self, name: str, **labels: object) -> list[float]:
        return list(self._observations.get(_obs_key(name, labels), []))

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
