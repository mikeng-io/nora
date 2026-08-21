from types import SimpleNamespace

from nora.agent.tag_trace_extract import extract_tag_trace
from nora.obs.metrics import Metrics


def _agent(messages):
    return SimpleNamespace(messages=messages)


def test_extracts_only_messages_after_the_baseline():
    agent = _agent([
        {"role": "system", "content": [{"text": "old"}]},  # baseline=1, excluded
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ])
    trace = extract_tag_trace(
        agent, baseline=1, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="send_reply", max_chars=10_000, metrics=Metrics(),
    )
    assert trace.messages == (
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    )

def test_strips_synthetic_artifacts_before_returning():
    agent = _agent([
        {"role": "assistant", "content": [
            {"text": "Reply already sent — the turn is finished."}
        ]},
    ])
    trace = extract_tag_trace(
        agent, baseline=0, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="vetoed", max_chars=10_000, metrics=Metrics(),
    )
    assert trace.messages == ()

def test_a_missing_messages_attribute_degrades_to_an_empty_trace_not_a_raise():
    agent = SimpleNamespace()  # no .messages at all -- simulates a renamed SDK field
    trace = extract_tag_trace(
        agent, baseline=0, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="llm_error", max_chars=10_000, metrics=Metrics(),
    )
    assert trace.messages == ()

def test_counts_truncations_on_the_declared_metric():
    metrics = Metrics()
    long_text = "x" * 20_000
    agent = _agent([{"role": "assistant", "content": [{"text": long_text}]}])
    extract_tag_trace(
        agent, baseline=0, tag_id="t1", conversation_id="c1", transcript=(),
        outcome="send_reply", max_chars=100, metrics=metrics,
    )
    assert metrics.get(
        "nora_tagtrace_truncations_total", what="assistant_narration",
    ) == 1
