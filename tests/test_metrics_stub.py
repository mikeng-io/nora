"""The `Metrics` wrapper's own behaviour — the stub interface M4/M5 record
through. Exercises real declared metric names only: prometheus-client
requires every series to be declared (name + labelnames) upfront, so an
arbitrary undeclared name like `"x"` cannot be invented on the fly.
"""

from nora.obs.metrics import Metrics


def test_counters_accumulate():
    m = Metrics()
    m.inc("nora_tags_debounced_total")
    m.inc("nora_tags_debounced_total")
    assert m.get("nora_tags_debounced_total") == 2


def test_labels_are_distinct_series():
    m = Metrics()
    m.inc("nora_silent_failures_total", reason="llm_error")
    m.inc("nora_silent_failures_total", reason="empty_reply")
    m.inc("nora_silent_failures_total", reason="llm_error")
    assert m.get("nora_silent_failures_total", reason="llm_error") == 2
    assert m.get("nora_silent_failures_total", reason="empty_reply") == 1


def test_unset_series_reads_as_zero():
    assert Metrics().get("nora_tags_debounced_total") == 0


def test_histograms_record_observations():
    m = Metrics()
    m.observe("nora_tag_latency_seconds", 0.4)
    m.observe("nora_tag_latency_seconds", 0.6)
    assert m.observations("nora_tag_latency_seconds") == [0.4, 0.6]


def test_gauges_overwrite():
    m = Metrics()
    m.gauge("nora_last_event_age_seconds", 5.0)
    m.gauge("nora_last_event_age_seconds", 9.0)
    assert m.get("nora_last_event_age_seconds") == 9.0


def test_snapshot_includes_recorded_counters():
    m = Metrics()
    m.inc(
        "nora_silent_failures_total", reason="empty_reply", platform="signal", audience="tag",
    )
    assert m.snapshot()[
        ("nora_silent_failures_total",
         (("audience", "tag"), ("platform", "signal"), ("reason", "empty_reply")))
    ] == 1.0


def test_a_fresh_instance_starts_clean():
    m = Metrics()
    m.inc("nora_tags_debounced_total")
    assert Metrics().get("nora_tags_debounced_total") == 0


def test_get_without_a_label_sums_across_it():
    """A query that omits a declared label marginalizes over it rather than
    requiring an exact match — this is what lets an un-migrated call site
    (recorded under the label's `""` default) and a migrated one both count
    toward `metrics.get(name, stage="ambient")`-style assertions elsewhere in
    the suite."""
    m = Metrics()
    m.inc("nora_errors_total", stage="ambient", platform="signal")
    m.inc("nora_errors_total", stage="ambient", platform="telegram")
    assert m.get("nora_errors_total", stage="ambient") == 2
    assert m.get("nora_errors_total", stage="ambient", platform="signal") == 1


def test_label_order_does_not_create_a_new_series():
    m = Metrics()
    m.inc("nora_errors_total", stage="ambient", platform="signal")
    m.inc("nora_errors_total", platform="signal", stage="ambient")
    assert m.get("nora_errors_total", stage="ambient", platform="signal") == 2
