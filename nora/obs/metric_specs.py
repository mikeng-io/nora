"""Every metric Nora declares — the names, labels and help text, nothing else.

Split out of `nora.obs.metrics` when that module hit 299 of the 300-line hard
cap (`tests/architecture/test_module_size.py`) with three features queued
behind it. The seam is real regardless of the line count: WHAT is measured is
a slow-moving registry the whole codebase reads, while HOW it is recorded is
one class with a prometheus-client registry inside it.

**Metric names are append-only.** This is an SRE's repo — a rename breaks
dashboards and alerts silently, and `tests/test_metrics_export.py` enumerates
`DECLARED_NAMES` to enforce it. Add to the end of a tuple; never edit a name.
"""

from __future__ import annotations

from typing import NamedTuple

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
    # Lazy adoption. A backfill that silently does nothing is an R-5 failure;
    # nora/app/adoption.py documents why these two are not redundant (a failed
    # feed is left unmarked on purpose, so it retries on the next start).
    _CounterSpec(
        "nora_lazy_adoptions_total",
        "Unattributed audit rows fed and marked by the startup backfill.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_adoption_failures_total",
        "Adoptable rows whose Honcho feed failed; left unmarked to retry.",
        ("platform",),
    ),
    # Progress reactions (nora/engagement/stages/indicate.py). Reactions are
    # decoration and every failure of one is swallowed so it can never touch
    # the reply — which is exactly why they need a counter, or "she stopped
    # showing the eyes" becomes another unobservable silence. One name, three
    # labels, because the interesting questions are all cross-cuts of the same
    # series: `phase` (ack/done/gave_up/clear) says WHICH indicator, `status`
    # (sent/failed/unsupported) says how it went. `unsupported` is not a
    # failure — it is the steady state of any platform without reactions, and
    # summing it into `failed` would make a correctly-degrading adapter look
    # broken forever.
    _CounterSpec(
        "nora_reactions_total",
        "Progress-indicator reactions attempted, by lifecycle phase and outcome.",
        ("platform", "phase", "status"),
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
