from nora.obs.metrics import DECLARED_NAMES, Metrics

# Every metric name that existed before this module's prometheus-client
# rewrite (spec §9 / Phase 6 Task 1), copied verbatim from the old `DECLARED`
# dict. Metric names are append-only in this repo — renaming one breaks
# dashboards — so this list is the literal contract `render()` must keep
# satisfying, independent of whatever `DECLARED_NAMES` says internally.
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
    """The name-preservation gate: every metric that existed before this
    rewrite still appears in `/metrics` output, by name, exactly. A labeled
    counter with no observations yet only emits its `# HELP`/`# TYPE` header
    (prometheus-client suppresses the child series until first use), so this
    checks those header lines rather than requiring a value line — which
    would otherwise force firing every series first just to prove its name
    survived.

    PROBE A (see task notes): renaming any one metric in the `_COUNTERS` /
    `_GAUGES` / `_HISTOGRAMS` tables in `nora/obs/metrics.py` makes exactly
    this assertion fail.
    """
    text = Metrics().render()
    for name in PRESERVED_METRIC_NAMES:
        assert f"TYPE {name} " in text, f"{name} missing from /metrics"


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


def test_gauges_render_as_gauges():
    m = Metrics()
    m.gauge("nora_last_event_age_seconds", 12.5)
    text = m.render()
    assert "# TYPE nora_last_event_age_seconds gauge" in text
    assert "nora_last_event_age_seconds 12.5" in text


def test_the_m4_call_sites_are_unchanged():
    """The stub interface is preserved exactly — no call site edits in M4/M5."""
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
    """spec §6: with N adapters, only nora_messages_total could tell you
    which platform was failing. Adding `platform` to these four closes that
    gap — backward-compatible because it is a new label, not a rename."""
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
