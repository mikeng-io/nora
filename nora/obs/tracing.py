"""OTel tracing setup (spec C.9). Strands instruments the agent loop/tools/model calls
itself; this module only configures the exporter and resource attributes.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

log = logging.getLogger("nora.tracing")


def setup_tracing(
    *, enabled: bool, exporter: str, endpoint: str, service_name: str = "nora"
) -> None:
    if not enabled:
        return
    try:
        span_exporter = _build_exporter(exporter, endpoint)
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        log.warning("tracing setup failed; continuing without tracing", exc_info=True)


def _build_exporter(exporter: str, endpoint: str):
    if exporter == "console":
        return ConsoleSpanExporter()
    if exporter == "file":
        return ConsoleSpanExporter(out=open(endpoint, "a", encoding="utf-8"))
    if exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=endpoint)
    raise ValueError(f"unknown tracing exporter: {exporter!r}")


def trace_attributes(ctx, persona) -> dict:
    """Provenance only — never message content (see the dedicated test for this)."""
    return {
        "tag_id": ctx.tag_id,
        "conversation_id": ctx.message.conversation_id,
        "persona_sha256": persona.sha256,
    }
