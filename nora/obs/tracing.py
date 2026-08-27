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
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.export import SpanExporter as _SpanExporter
from opentelemetry.trace import Status, StatusCode
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


@contextmanager
def span(name: str, **attributes: object) -> Iterator[None]:
    """One named span, and **never load-bearing** — the same rule `setup_tracing`
    keeps one function up.

    Until 2026-08-27 `nora/` opened **zero** spans: every span reaching Langfuse
    came from Strands, from inside the agent loop, so a turn that failed BEFORE
    the model call produced no trace at all and was indistinguishable from a turn
    that never happened (`design/dreamer-agent-loop/02-instrument-blindness`
    Layer 7). This is the skeleton that closes it — one span per turn, children
    named for the stages that already exist, so a turn ending early ends
    **visibly, at a named stage**.

    **It catches `BaseException`, records, and RE-RAISES.** Three properties, and
    all three are load-bearing:

    - `BaseException`, not `Exception`, because **cancellation is not an
      `Exception`** — a wrapper that only catches `Exception` leaves the cancel
      path unrecorded, which is the defect the Standing debrief shipped and its
      review caught. A cancelled turn is exactly the turn worth seeing.
    - It **re-raises, always**. An observation that changes control flow is not an
      observation. A span must never turn a failing turn into a passing one, and
      must never swallow a cancel.
    - The *recording itself* is wrapped, because a broken tracer must not break a
      turn. If setting an attribute throws, the turn continues untraced.

    A no-op when tracing is off: `get_tracer` returns a non-recording tracer, so
    this costs a context manager and nothing else.
    """
    try:
        cm = trace.get_tracer("nora").start_as_current_span(name)
        sp = cm.__enter__()
    except Exception:
        # ACQUISITION is guarded too, not just recording. The debrief stage runs
        # inside a `finally` AFTER the tag pipeline's outer `except BaseException`
        # has already run, so anything raised there escapes `run()` and takes out
        # the whole tag rather than just the trace. A span must never be able to
        # do that. Untraced body still runs.
        log.debug("span %s could not start; running untraced", name, exc_info=True)
        yield
        return
    try:
        try:
            for key, value in attributes.items():
                safe = value if isinstance(value, (str, bool, int, float)) else str(value)
                sp.set_attribute(key, safe)
        except Exception:  # a broken tracer must not break a turn
            log.debug("span attributes not recorded for %s", name, exc_info=True)
        try:
            yield
        except BaseException as exc:
            try:
                sp.set_attribute("nora.outcome", type(exc).__name__)
                sp.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                if isinstance(exc, Exception):
                    sp.record_exception(exc)
            except Exception:
                log.debug("span error not recorded for %s", name, exc_info=True)
            raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            # Same reason: a failing exporter on span end must not surface as a
            # tag failure. See the acquisition guard above.
            log.debug("span %s did not close cleanly", name, exc_info=True)


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


def trace_attributes(ctx, doctrine) -> dict:
    """Provenance only — never message content (see the dedicated test for this).

    `note_index_entries` is a COUNT, which is why it belongs in a dict that
    otherwise holds only hashes and ids: it says how many notes were in front
    of her on this turn without saying what any of them is. The titles are
    already in the GENERATION span's prompt for a turn that had any; this
    number is the one fact a turn with NONE cannot otherwise report, and a
    trace that cannot say «she was shown nothing» reads identically to one
    where the tier was never wired.

    It is the per-turn counterpart of `nora_note_index_entries`, and the two
    answer different questions on purpose: the gauge says what the notebook
    holds NOW, this says what THIS turn saw. They diverge exactly when a turn
    matters — a reply written against an index that has since changed.

    Stamped on every span in the turn (Strands applies these to the agent span
    and each tool/model child), so filtering Langfuse to «turns that saw an
    empty notebook» costs no new instrumentation at the call sites.
    """
    return {
        "tag_id": ctx.tag_id,
        "conversation_id": ctx.message.conversation_id,
        "doctrine_sha256": doctrine.sha256,
        "note_index_entries": len(ctx.notes),
    }
