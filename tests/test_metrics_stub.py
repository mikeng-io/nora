from nora.obs.metrics import Metrics


def test_counters_accumulate():
    m = Metrics()
    m.inc("nora_tags_total")
    m.inc("nora_tags_total")
    assert m.get("nora_tags_total") == 2


def test_labels_are_distinct_series():
    m = Metrics()
    m.inc("nora_silent_failures_total", reason="llm_error")
    m.inc("nora_silent_failures_total", reason="empty_reply")
    m.inc("nora_silent_failures_total", reason="llm_error")
    assert m.get("nora_silent_failures_total", reason="llm_error") == 2
    assert m.get("nora_silent_failures_total", reason="empty_reply") == 1


def test_unset_series_reads_as_zero():
    assert Metrics().get("nora_tags_total") == 0


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


def test_snapshot_is_stable_for_assertions():
    m = Metrics()
    m.inc("a", reason="x")
    assert m.snapshot() == {("a", (("reason", "x"),)): 1.0}


def test_reset_clears_everything():
    m = Metrics()
    m.inc("a")
    m.reset()
    assert m.snapshot() == {}


def test_label_order_does_not_create_a_new_series():
    m = Metrics()
    m.inc("x", a="1", b="2")
    m.inc("x", b="2", a="1")
    assert m.get("x", a="1", b="2") == 2
