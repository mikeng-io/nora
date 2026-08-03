from nora.obs.metrics import DECLARED, Metrics


def test_every_spec_c9_series_is_declared():
    for name in (
        "nora_messages_total", "nora_tags_total", "nora_tags_debounced_total",
        "nora_replies_total", "nora_errors_total", "nora_silent_failures_total",
        "nora_guard_vetoes_total", "nora_leak_blocks_total",
        "nora_persona_reload_failures_total", "honcho_feed_success", "honcho_feed_failure",
        "nora_tag_latency_seconds", "nora_llm_latency_seconds",
        "nora_last_event_age_seconds",
    ):
        assert name in DECLARED, f"{name} is not declared"


def test_render_emits_help_and_type_lines():
    text = Metrics().render()
    assert "# HELP nora_tags_total" in text
    assert "# TYPE nora_tags_total counter" in text


def test_declared_counters_render_at_zero_before_first_use():
    assert "nora_tags_total 0" in Metrics().render()


def test_counters_render_with_labels():
    m = Metrics()
    m.inc("nora_silent_failures_total", reason="llm_error")
    assert 'nora_silent_failures_total{reason="llm_error"} 1' in m.render()


def test_label_values_are_escaped():
    m = Metrics()
    m.inc("nora_errors_total", stage='we"ird\\')
    assert 'stage="we\\"ird\\\\"' in m.render()


def test_histograms_render_buckets_sum_and_count():
    m = Metrics()
    m.observe("nora_tag_latency_seconds", 0.4)
    m.observe("nora_tag_latency_seconds", 7.0)
    text = m.render()
    assert "# TYPE nora_tag_latency_seconds histogram" in text
    assert 'nora_tag_latency_seconds_bucket{le="0.5"} 1' in text
    assert 'nora_tag_latency_seconds_bucket{le="+Inf"} 2' in text
    assert "nora_tag_latency_seconds_sum 7.4" in text
    assert "nora_tag_latency_seconds_count 2" in text


def test_gauges_render_as_gauges():
    m = Metrics()
    m.gauge("nora_last_event_age_seconds", 12.5)
    text = m.render()
    assert "# TYPE nora_last_event_age_seconds gauge" in text
    assert "nora_last_event_age_seconds 12.5" in text


def test_the_m4_call_sites_are_unchanged():
    """The stub interface is preserved exactly — no call site edits in M4/M5."""
    m = Metrics()
    m.inc("nora_tags_total")
    m.observe("nora_tag_latency_seconds", 1.0)
    m.gauge("nora_last_event_age_seconds", 2.0)
    assert m.get("nora_tags_total") == 1
    assert m.observations("nora_tag_latency_seconds") == [1.0]


def test_render_is_parseable_as_a_whole():
    m = Metrics()
    m.inc("nora_tags_total")
    for line in m.render().splitlines():
        assert line.startswith("#") or " " in line
