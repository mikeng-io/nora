"""OTel tracing setup for Smith's own process. Strands instruments the agent
loop/tools/model calls itself once a global `TracerProvider` exists
(confirmed against the installed `strands-agents==1.50.2`:
`telemetry/tracer.py`'s `Tracer.__init__` calls the standard
`opentelemetry.trace.get_tracer_provider()`), so this module only configures
the exporter and resource attributes -- the same division of labour
`nora/obs/tracing.py` draws for Nora's own process.

Deliberately standalone, not an import of `nora.obs.tracing` -- this image
never imports the top-level `nora` package (`agent.py`'s own docstring). The
shape is duplicated on purpose; the two modules instrument two different
processes and are allowed to drift independently.

Unlike `nora.obs.tracing`, there is no `console`/`file` exporter choice here
-- Smith runs once per job, non-interactively, with no operator watching a
console; `otlp_http` (Langfuse) is the only destination that makes sense.
`endpoint`/`headers` arrive as already-resolved values (env vars
`LocalDockerBackend` injects directly), not `*_env` names -- this process has
no access to Nora's own `.env` to resolve a name from.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.util.re import parse_env_headers

log = logging.getLogger("smith.tracing")


def setup_tracing(*, enabled: bool, endpoint: str, headers: str) -> None:
    if not enabled or not endpoint:
        return
    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, headers=_parse_headers(headers))
        provider = TracerProvider(resource=Resource.create({"service.name": "smith"}))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        # Never load-bearing -- a bad endpoint or unparsable headers value
        # must not stop a job from doing its actual work, the same
        # discipline nora/obs/tracing.py's own setup_tracing holds.
        log.warning("tracing setup failed; continuing without tracing", exc_info=True)


def _parse_headers(raw: str) -> dict[str, str] | None:
    """`raw` is OTel's own `OTEL_EXPORTER_OTLP_HEADERS` shape: comma-separated
    `key=value` pairs, URL-encoded. Empty means "send no auth header", not
    an error."""
    if not raw:
        return None
    return dict(parse_env_headers(raw))
