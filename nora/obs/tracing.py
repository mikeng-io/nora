"""OTel tracing setup (spec C.9). Strands instruments the agent loop/tools/model calls
itself; this module only configures the exporter and resource attributes.

The OTLP exporter is HTTP/protobuf, not gRPC: Langfuse's collector endpoint
(`/api/public/otel/v1/traces`) speaks HTTP, and `opentelemetry-exporter-otlp-
proto-grpc` was never installed to begin with (Phase 4's mypy override covered
exactly that missing import). `otlp_http` replaces the old `otlp` config value
outright rather than joining it — a gRPC option that nothing installs and
nothing can ever reach is worse than no option, and there was never a
production config using it (the live `config.toml` has always been `file`).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.export import SpanExporter as _SpanExporter
from opentelemetry.util.re import parse_env_headers

log = logging.getLogger("nora.tracing")


def setup_tracing(
    *, enabled: bool, exporter: str, endpoint: str, headers_env: str = "",
    service_name: str = "nora",
) -> None:
    if not enabled:
        return
    try:
        span_exporter = _build_exporter(exporter, endpoint, headers_env)
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        # Tracing is never load-bearing — a bad endpoint, an unparsable headers_env
        # value, or (once spans actually flow) an unreachable collector must never
        # take the app down. Setup-time failures land here; export-time failures
        # (the network call itself) happen later on BatchSpanProcessor's own
        # background thread, which the OTel SDK already isolates from the app —
        # see tests/test_tracing.py's PROBE-derived test for the latter.
        log.warning("tracing setup failed; continuing without tracing", exc_info=True)


def _resolve_headers(headers_env: str) -> dict[str, str] | None:
    """`headers_env` names an environment variable (DEC-8: `*_env`, never the
    value inlined, no `${VAR}` interpolation); its content is OTel's own
    `OTEL_EXPORTER_OTLP_HEADERS` shape — comma-separated `key=value` pairs,
    URL-encoded. Empty name or empty/unset value both mean "send no auth
    header", not an error.
    """
    if not headers_env:
        return None
    raw = os.environ.get(headers_env, "")
    if not raw:
        return None
    return dict(parse_env_headers(raw))


def _build_exporter(exporter: str, endpoint: str, headers_env: str) -> _SpanExporter:
    if exporter == "console":
        return ConsoleSpanExporter()
    if exporter == "file":
        return ConsoleSpanExporter(out=open(endpoint, "a", encoding="utf-8"))
    if exporter == "otlp_http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=endpoint, headers=_resolve_headers(headers_env))
    raise ValueError(f"unknown tracing exporter: {exporter!r}")


def trace_attributes(ctx, persona) -> dict:
    """Provenance only — never message content (see the dedicated test for this)."""
    return {
        "tag_id": ctx.tag_id,
        "conversation_id": ctx.message.conversation_id,
        "persona_sha256": persona.sha256,
    }
