from nora.obs.metrics import DECLARED_NAMES, Metrics

# Every metric name that must keep rendering, as literals. Metric names are
# append-only in this repo — renaming one breaks dashboards — so this list is
# the literal contract `render()` must keep satisfying, independent of whatever
# `DECLARED_NAMES` says internally.
PRESERVED_METRIC_NAMES = frozenset({
    "nora_messages_total",
    "nora_tags_total",
    "nora_tags_debounced_total",
    "nora_tags_dropped_total",
    "nora_replies_total",
    "nora_errors_total",
    "nora_silent_failures_total",
    "nora_guard_vetoes_total",
    "nora_leak_blocks_total",
    "nora_doctrine_reload_failures_total",
    "honcho_feed_success",
    "honcho_feed_failure",
    "nora_tag_latency_seconds",
    "nora_llm_latency_seconds",
    "nora_overhead_seconds",
    "nora_last_event_age_seconds",
    "nora_store_busy_total",
    "nora_adapter_restarts_total",
})


def test_every_pre_rewrite_metric_name_is_still_declared():
    assert PRESERVED_METRIC_NAMES <= DECLARED_NAMES


def test_every_preserved_name_still_renders():
    """The name-preservation gate: every metric in the list above appears in
    `/metrics` output, by name, exactly. A labeled counter with no observations
    yet only emits its `# HELP`/`# TYPE` header (prometheus-client suppresses
    the child series until first use), so this checks those header lines rather
    than requiring a value line — which would otherwise force firing every
    series first just to prove its name survived.

    Renaming any one metric in the `_COUNTERS` / `_GAUGES` / `_HISTOGRAMS`
    tables in `nora/obs/metrics.py` makes exactly this assertion fail.
    """
    text = Metrics().render()
    for name in PRESERVED_METRIC_NAMES:
        assert f"TYPE {name} " in text, f"{name} missing from /metrics"


def test_the_cognition_stimulus_metrics_render_under_their_renamed_names():
    """DEC-234: the series are `nora_stimulus_total` and
    `nora_stimulus_pending_age_seconds`. The earlier `nora_signal_*` names are
    gone outright, not aliased — the one stated exception to metric names being
    append-only, made before any dashboard depended on them."""
    text = Metrics().render()
    assert "TYPE nora_stimulus_total " in text
    assert "TYPE nora_stimulus_pending_age_seconds " in text
    assert "nora_signal_total" not in text
    assert "nora_signal_pending_age_seconds" not in text


def test_prometheus_client_does_not_double_the_total_suffix():
    """prometheus-client auto-appends `_total` to a Counter's name unless it
    already ends with `_total` — every declared counter name here already
    does, so the rendered name must be exactly what was declared, never
    `..._total_total`, and never silently stripped back to the bare name."""
    text = Metrics().render()
    assert "# TYPE nora_tags_debounced_total counter" in text
    assert "nora_tags_debounced_total_total" not in text


def test_render_emits_help_and_type_lines():
    text = Metrics().render()
    assert "# HELP nora_tags_debounced_total" in text
    assert "# TYPE nora_tags_debounced_total counter" in text


def test_declared_unlabeled_counters_render_at_zero_before_first_use():
    assert "nora_tags_debounced_total 0.0" in Metrics().render()


def test_counters_render_with_labels():
    m = Metrics()
    m.inc(
        "nora_silent_failures_total", reason="llm_error", platform="signal", audience="tag",
    )
    text = m.render()
    assert (
        'nora_silent_failures_total{audience="tag",platform="signal",reason="llm_error"} 1.0'
        in text
    )


def test_label_values_are_escaped():
    m = Metrics()
    m.inc("nora_errors_total", stage='we"ird\\', platform="signal")
    text = m.render()
    assert 'stage="we\\"ird\\\\"' in text


def test_histograms_render_buckets_sum_and_count():
    m = Metrics()
    m.observe("nora_tag_latency_seconds", 0.4)
    m.observe("nora_tag_latency_seconds", 7.0)
    text = m.render()
    assert "# TYPE nora_tag_latency_seconds histogram" in text
    assert 'nora_tag_latency_seconds_bucket{le="0.5"} 1.0' in text
    assert 'nora_tag_latency_seconds_bucket{le="+Inf"} 2.0' in text
    assert "nora_tag_latency_seconds_sum 7.4" in text
    assert "nora_tag_latency_seconds_count 2.0" in text


def test_the_per_tag_message_histogram_is_bucketed_for_counts_not_seconds():
    """It shares `observe()` with three latency histograms, so it would
    silently inherit their `0.1 … 30` seconds buckets — under which 1 and 2
    messages land in the same bucket and the whole series says nothing. The
    number it exists to make legible (step 5: does the cap become a quota
    always spent in full?) is only visible if consecutive integers separate."""
    m = Metrics()
    for delivered in (0, 1, 1, 2):
        m.observe("nora_messages_per_tag", delivered)
    text = m.render()
    assert "# TYPE nora_messages_per_tag histogram" in text
    assert 'nora_messages_per_tag_bucket{le="0.0"} 1.0' in text
    assert 'nora_messages_per_tag_bucket{le="1.0"} 3.0' in text
    assert 'nora_messages_per_tag_bucket{le="2.0"} 4.0' in text
    assert "nora_messages_per_tag_count 4.0" in text


def test_a_labelled_histogram_renders_one_series_per_label_value():
    """`nora_llm_latency_seconds` is the first histogram to carry a label, and
    it carries it because two paths with two different timeouts observe into it
    (`[llm] timeout_seconds` vs `[heartbeat] timeout_seconds`). A histogram that
    accepted the keyword and dropped it would leave every existing assertion
    green while the samples stayed mixed."""
    m = Metrics()
    m.observe("nora_llm_latency_seconds", 0.4, audience="tag")
    m.observe("nora_llm_latency_seconds", 7.0, audience="heartbeat")
    text = m.render()
    assert 'nora_llm_latency_seconds_bucket{audience="tag",le="0.5"} 1.0' in text
    assert 'nora_llm_latency_seconds_bucket{audience="tag",le="+Inf"} 1.0' in text
    assert 'nora_llm_latency_seconds_bucket{audience="heartbeat",le="+Inf"} 1.0' in text
    assert 'nora_llm_latency_seconds_count{audience="tag"} 1.0' in text
    assert 'nora_llm_latency_seconds_count{audience="heartbeat"} 1.0' in text


def test_observations_sum_over_the_label_dimensions_a_caller_omits():
    """`observations()` matches labels the way `get()` does — partially — so a
    caller that names no label still sees every sample. That is what keeps
    "how many model calls happened at all" answerable after the split."""
    m = Metrics()
    m.observe("nora_llm_latency_seconds", 0.4, audience="tag")
    m.observe("nora_llm_latency_seconds", 7.0, audience="heartbeat")

    assert m.observations("nora_llm_latency_seconds") == [0.4, 7.0]
    assert m.observations("nora_llm_latency_seconds", audience="heartbeat") == [7.0]


def test_the_audience_label_is_on_both_shared_llm_series():
    """The tag-path series a fire turn must not silently move include these two
    shared LLM series. Names are append-only; labels are not (`metric_specs.py`
    records `platform` being added to four existing series)."""
    m = Metrics()
    m.inc("nora_llm_stop_reason_total", reason="end_turn", audience="heartbeat")
    text = m.render()
    assert (
        'nora_llm_stop_reason_total{audience="heartbeat",reason="end_turn"} 1.0' in text
    )


def test_gauges_render_as_gauges():
    m = Metrics()
    m.gauge("nora_last_event_age_seconds", 12.5)
    text = m.render()
    assert "# TYPE nora_last_event_age_seconds gauge" in text
    assert "nora_last_event_age_seconds 12.5" in text


def test_the_m4_call_sites_are_unchanged():
    """The stub interface is preserved exactly, so call sites need no edits."""
    m = Metrics()
    m.inc("nora_tags_debounced_total")
    m.observe("nora_tag_latency_seconds", 1.0)
    m.gauge("nora_last_event_age_seconds", 2.0)
    assert m.get("nora_tags_debounced_total") == 1
    assert m.observations("nora_tag_latency_seconds") == [1.0]


def test_render_is_parseable_as_a_whole():
    m = Metrics()
    m.inc("nora_tags_debounced_total")
    for line in m.render().splitlines():
        assert line.startswith("#") or " " in line


def test_platform_label_present_on_the_four_named_series():
    """With N adapters, only nora_messages_total could tell you which platform
    was failing. Adding `platform` to these four closes that gap —
    backward-compatible because it is a new label, not a rename."""
    m = Metrics()
    m.inc("nora_tags_total", platform="signal")
    m.inc("nora_replies_total", status="sent", platform="signal")
    m.inc(
        "nora_silent_failures_total", reason="empty_reply", platform="signal", audience="tag",
    )
    m.inc("nora_errors_total", stage="ambient", platform="signal")
    text = m.render()
    assert 'nora_tags_total{platform="signal"} 1.0' in text
    assert 'nora_replies_total{platform="signal",status="sent"} 1.0' in text
    assert (
        'nora_silent_failures_total{audience="tag",platform="signal",reason="empty_reply"} 1.0'
        in text
    )
    assert 'nora_errors_total{platform="signal",stage="ambient"} 1.0' in text


def test_an_unmigrated_call_site_still_records_under_an_empty_platform():
    """A call site that doesn't pass `platform=` yet (or structurally can't —
    a background sweep with no platform of its own) must not crash; it
    degrades to `platform=""` rather than being rejected."""
    m = Metrics()
    m.inc("nora_errors_total", stage="prune")
    assert m.get("nora_errors_total", stage="prune") == 1
    assert 'nora_errors_total{platform="",stage="prune"} 1.0' in m.render()


def test_the_media_join_histograms_can_measure_a_batch_path():
    """The buckets are sized for the values the series carries: a tag window
    (1s, 40 blobs) and also adoption's 1000-row backfill and the Dreamer's
    batch. A histogram whose top bucket is two orders of magnitude under the
    value it must record reports every batch path as `+Inf` — unmeasurable,
    which is the exact state the `path` label exists to end.

    Asserted on the DECLARED buckets rather than on rendered output, because
    the defect is in the sizing and a rendered series would only show it once
    something real saturated it."""
    from nora.obs.metric_specs import _HISTOGRAMS

    by_name = {h.name: h for h in _HISTOGRAMS}

    seconds = by_name["nora_media_join_seconds"]
    assert max(seconds.buckets) >= 10, (
        "a batch join cannot be distinguished from a slow one"
    )
    assert seconds.labelnames == ("path",)

    blobs = by_name["nora_media_blobs_joined"]
    assert max(blobs.buckets) >= 500, (
        "the histogram cannot record the count needed to size "
        "DEFAULT_QUERY_BLOBS, which its own help text says it exists to size"
    )
    assert blobs.labelnames == ("path",)
