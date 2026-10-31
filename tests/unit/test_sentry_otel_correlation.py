"""Sentry↔OTel correlation (ADR 0034): an error event captured inside a live
span must carry the SAME trace_id/span_id the span exported.

`instrumenter="otel"` ALONE does not do this — measured on sentry-sdk 2.44.0,
an event without the span processor invents its own trace id. The processor
`nora.obs.errors.otel_span_processor` supplies is what correlates, and this
test proves it against an in-memory transport and a real OTel provider — no
Sentry event ever leaves the process.
"""

from __future__ import annotations

import logging

import pytest

sentry_sdk = pytest.importorskip("sentry_sdk", reason="sentry extra not installed")
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from sentry_sdk.integrations.logging import LoggingIntegration  # noqa: E402
from sentry_sdk.transport import Transport  # noqa: E402

from nora.obs.errors import otel_span_processor, setup_errors  # noqa: E402


class _MemTransport(Transport):
    def __init__(self) -> None:
        self.events: list[dict] = []

    def capture_event(self, event) -> None:
        self.events.append(event)

    def capture_envelope(self, envelope) -> None:
        for item in envelope.items:
            self.events.append(item.payload.json)


def _drop_client() -> None:
    # set_client(None) detaches without re-running init — init() (even
    # init(None)) rescans integrations and raises on the optional-dependency
    # trap nora/obs/errors.py names (starlette present, jinja2 absent).
    sentry_sdk.get_global_scope().set_client(None)


@pytest.fixture()
def sentry_capture():
    """init sentry with a capturing transport, detach the client after."""
    transport = _MemTransport()
    sentry_sdk.init(
        dsn="https://k@example.ingest.sentry.io/1",
        transport=transport,
        traces_sample_rate=0.0,
        instrumenter="otel",
        integrations=[
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR)
        ],
        auto_enabling_integrations=False,
    )
    yield transport
    _drop_client()


def test_otel_span_processor_is_supplied_for_otel_instrumented_sentry(sentry_capture):
    assert otel_span_processor() is not None


def test_otel_span_processor_is_none_without_an_active_client():
    _drop_client()
    assert otel_span_processor() is None


def test_error_event_carries_the_live_spans_ids(sentry_capture):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    bridge = otel_span_processor()
    assert bridge is not None, "bridge missing — instrumenter not honoured"
    provider.add_span_processor(bridge)
    tracer = provider.get_tracer("nora.test")

    with tracer.start_as_current_span("proof") as sp:
        ctx = sp.get_span_context()
        otel_ids = (format(ctx.trace_id, "032x"), format(ctx.span_id, "016x"))
        try:
            raise RuntimeError("correlation proof")
        except RuntimeError:
            logging.getLogger("nora.test").exception("boom")

    spans = exporter.get_finished_spans()
    assert spans, "the span must actually export for ids to correlate"
    assert len(sentry_capture.events) == 1, "one exception, one event"
    tc = (sentry_capture.events[0].get("contexts") or {}).get("trace") or {}
    assert tc.get("trace_id") == otel_ids[0], (
        f"event trace_id {tc.get('trace_id')} != exported {otel_ids[0]} — "
        "Sentry invented its own trace id"
    )
    assert tc.get("span_id") == otel_ids[1]


def test_setup_errors_reports_off_without_dsn():
    assert setup_errors(enabled=True, dsn_env="NORA_TEST_NO_DSN",
                        credentials={}) is False
