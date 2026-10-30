"""Sentry, for the one question the rest of this package cannot answer.

`docs/observability.md` states the boundary: Prometheus answers "how many, how
often, with what outcome", Langfuse holds the traces with a model call in them,
and logs are forensic. All three answer questions about things that happened;
none groups many occurrences of the same stack trace into one issue, which is
what this module is for. It does not mark an issue as new since the last deploy
unless `setup_errors` is given a `release` (`__main__` passes none), and it does
not capture a frame's local variables (`include_local_variables=False`).

**Errors only: `traces_sample_rate` is 0.** `sentry_sdk` can instrument spans,
but that would give this process two tracing pipelines writing two
incompatible trace ids for the same turn. Tracing belongs to `nora.obs.tracing`
and lands in Langfuse. Sentry gets exceptions.

What it will and will not see:

* it SEES every `log.exception` site: the `LoggingIntegration` captures a
  record logged at ERROR with `exc_info`, plus anything that escapes to the
  top;
* it does NOT see an R-27 `unavailable`. This codebase is built not to raise,
  so its characteristic failure is a counter label, not a traceback. Those are
  Prometheus' to catch (`deploy/prometheus/`).

Never load-bearing, like every other instrument here: a missing DSN, an
unreachable Sentry, an SDK that will not import — each costs error reporting
and nothing else.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nora.secret import resolve

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import SpanProcessor

log = logging.getLogger("nora.errors")


def otel_span_processor() -> "SpanProcessor | None":
    """The OTel↔Sentry bridge, for the process's `TracerProvider` to attach.

    `instrumenter="otel"` alone does NOT correlate: without the SDK's span
    processor, an error event invents its own trace id (measured on
    sentry-sdk 2.44.0 — an exception inside a live OTel span carried an
    unrelated `contexts.trace.trace_id`). Attached to OUR provider, the
    processor registers each OTel span with Sentry's scope machinery, so the
    event carries the SAME `trace_id`/`span_id` the span exported — one id
    space, one provider, no second sampler (`traces_sample_rate` stays 0;
    the processor links ids, it does not mint transactions).

    `None` when the SDK is absent, uninitialised, or not otel-instrumented —
    the caller just does not attach it. Optional dependency, never
    load-bearing.
    """
    try:
        import sentry_sdk
        from sentry_sdk.integrations.opentelemetry import SentrySpanProcessor
    except ImportError:
        return None
    try:
        options = getattr(sentry_sdk.get_client(), "options", None) or {}
        if options.get("instrumenter") != "otel":
            return None
        return SentrySpanProcessor()
    except Exception:
        log.debug("sentry: otel span processor not attached", exc_info=True)
        return None


def setup_errors(
    *,
    enabled: bool,
    dsn_env: str,
    environment: str = "",
    release: str = "",
    credentials: dict[str, str] | None = None,
) -> bool:
    """Wire Sentry for error capture. Returns whether it is ON.

    Every exit logs whether reporting is on and why, so "Sentry is configured"
    is a line in the log rather than an assumption.

    `dsn_env` NAMES an environment variable; the DSN is never inlined in
    `config.toml` (DEC-8), and an empty or unset value means OFF rather than an
    error, so a test run needs no Sentry account.
    """
    if not enabled:
        return False
    values = resolve(Path("secret.toml")) if credentials is None else credentials
    dsn = values.get(dsn_env, "").strip()
    if not dsn:
        log.info("sentry: %s is unset; error reporting is off", dsn_env)
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        # `sentry` is an optional extra; not installed is a deployment choice,
        # not a crash. Narrow on purpose: an SDK that is present but broken on
        # import should be loud.
        log.warning("sentry: sentry_sdk is not installed; error reporting is off")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment or "unknown",
            release=release or None,
            # Errors only; see the module docstring.
            traces_sample_rate=0.0,
            # Take trace context from the OTel provider rather than Sentry's
            # own: error events then carry the SAME trace_id/span_id the span
            # exported to the collector — one tracing pipeline, one id space.
            instrumenter="otel",
            # `event_level=ERROR` turns `log.exception` sites into Sentry
            # issues; `level=WARNING` is the BREADCRUMB floor, so an issue
            # arrives with the warnings that led to it.
            integrations=[
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
            # Auto-enabling integrations must stay off: the SDK wires one for
            # every package it finds (e.g. `starlette`, present here only as a
            # transitive dependency), and an integration whose own optional
            # dependency is absent (jinja2) makes `init` raise, which takes the
            # whole reporter with it. The one integration needed is passed
            # explicitly. `default_integrations` stays ON: those are the SDK's
            # core (excepthook, dedupe, atexit, stdlib) and make an unhandled
            # exception an event at all.
            auto_enabling_integrations=False,
            # `send_default_pii=False` does NOT keep a frame's local variable
            # values off the wire: `include_local_variables` defaults to
            # `True` independently of it. An ACI or a phone number in a local
            # is the identifier R-18 keeps off every other exit, so it is
            # turned off explicitly.
            include_local_variables=False,
            send_default_pii=False,
            attach_stacktrace=True,
        )
    except Exception:
        log.warning("sentry: init failed; error reporting is off", exc_info=True)
        return False
    log.info("sentry: error reporting on (environment=%s)", environment or "unknown")
    return True
