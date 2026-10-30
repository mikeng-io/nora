"""Focused tests for the SigNoz observability landing (ADR 0034):

* unknown usage on a raw model span stays UNKNOWN (no fabricated zeros);
* the served model / provider attributes land on the span;
* the Cognition session identity stamps a conversation id;
* the vision client's injected recorder produces a model span per call;
* structured log lines carry the live span's trace_id/span_id.
"""

from __future__ import annotations

from nora.agent.telemetry import (
    RawSpanRecorder,
    TraceIdentityProcessor,
    end_raw_model_span,
    start_raw_model_span,
)


def _strands_provider(monkeypatch):
    """An in-memory provider behind Strands' own tracer lookup.

    `exported_spans` patches `nora.obs.tracing`'s `trace.get_tracer`; Strands'
    `Tracer` resolves the provider through `trace_api.get_tracer_provider` and
    caches it in `_tracer_instance`, so it needs its own seam — the same one
    the issue's synthetic probe used.
    """
    import strands.telemetry.tracer as st
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(st.trace_api, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(st, "_tracer_instance", None)
    return exporter


def test_unknown_usage_writes_no_usage_attributes(monkeypatch):
    """A provider that omits usage must not read as "0 tokens" (R-27):
    `end_raw_model_span` used to stamp fabricated zeros through
    `end_model_invoke_span`; now the call leaves `gen_ai.usage.*` absent.
    PROBE: pass zeros back through `end_model_invoke_span` and the keys return."""
    exporter = _strands_provider(monkeypatch)
    span = start_raw_model_span(
        messages=[{"role": "user", "content": [{"text": "synthetic"}]}],
        model_id="synthetic-model",
    )
    end_raw_model_span(span=span, content="synthetic reply", finish_reason="stop")
    (recorded,) = exporter.get_finished_spans()
    assert not any(k.startswith("gen_ai.usage.") for k in recorded.attributes), (
        f"unknown usage was stamped as a number: {recorded.attributes}"
    )


def test_served_model_and_provider_are_distinguishable(monkeypatch):
    """The requested alias, the SERVED model and the provider lane are three
    different answers — pricing must never be guessed from the alias."""
    exporter = _strands_provider(monkeypatch)
    span = start_raw_model_span(
        messages=[{"role": "user", "content": [{"text": "synthetic"}]}],
        model_id="requested-alias",
    )
    end_raw_model_span(
        span=span, content="reply", finish_reason="stop",
        input_tokens=3, output_tokens=2, total_tokens=5,
        response_model="served-model", provider="omniroute",
    )
    (recorded,) = exporter.get_finished_spans()
    attrs = recorded.attributes
    assert attrs["gen_ai.request.model"] == "requested-alias"
    assert attrs["gen_ai.response.model"] == "served-model"
    assert attrs["gen_ai.provider.name"] == "omniroute"
    assert attrs["gen_ai.usage.input_tokens"] == 3


def test_cognition_session_stamps_conversation_identity():
    """Finding 3: cognition roots carried `nora.cognition.session_id` and got
    no `gen_ai.conversation.id`. The processor now translates it — a session
    is its own identity, never borrowed from a room."""
    proc = TraceIdentityProcessor()

    class _Fake:
        attributes = {"nora.cognition.session_id": "cog-42"}

        def __init__(self):
            self.stamped: dict = {}

        def set_attribute(self, k, v):
            self.stamped[k] = v

    span = _Fake()
    proc.on_start(span, parent_context=None)
    assert span.stamped["gen_ai.conversation.id"] == "cog-42"
    assert span.stamped["session.id"] == "cog-42"


def test_vision_recorder_produces_a_span_per_call():
    """The injected seam: `RawSpanRecorder.start/finish` are the same
    `start_raw_model_span`/`end_raw_model_span` pair the relevance gate uses —
    the vision client now has model telemetry without importing `nora.agent`."""
    rec = RawSpanRecorder()
    span = rec.start(
        messages=[{"role": "user", "content": [{"text": "describe"}]}],
        model_id="mimo", provider="omniroute",
    )
    assert span is not None
    rec.finish(
        span, content="a photo", finish_reason="stop",
        input_tokens=10, output_tokens=4, total_tokens=14,
        response_model="mimo-served", provider="omniroute",
    )


def test_log_lines_carry_trace_ids_only_inside_a_span(exported_spans):
    """Finding 4: JSON lines had tag_id/severity but no trace correlation.
    `_trace_ids` stamps the live span's ids; a line outside one gets none —
    absent means "no recording span", never an invented id."""
    from nora.obs.logging import _trace_ids
    from nora.obs.tracing import span as traced

    with traced("the.test.span"):
        inside = _trace_ids(None, "", {})
        assert inside.get("trace_id") and inside.get("span_id")
    assert _trace_ids(None, "", {}) == {}
