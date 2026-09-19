"""strands.telemetry, wrapped — the only other module besides
`nora.agent.structured` that may import `strands`
(`tests/test_boundaries.py::test_strands_import_boundary` allows the whole
`nora/agent/` package, nothing narrower).

**What lives here versus in `nora.obs.tracing`.** The R-76 sampler
(`_PlumbingIsNotATrace`/`build_sampler`), the plumbing-span helper (`span`),
context propagation (`capture_context`/`attached`), and per-turn attribute
building (`trace_attributes`) are pure OpenTelemetry with no Strands
dependency, and stay in `nora.obs.tracing` — most of the codebase calls them
and none of it may import `strands`. This module owns only the two things
that genuinely need `strands.telemetry` itself: provider/exporter setup
(`setup_agent_tracing`, replacing what `nora.obs.tracing.setup_tracing` used
to assemble by hand) and the span pair a RAW `chat.completions.create` call
needs to open/close in the SDK's own vocabulary
(`start_raw_model_span`/`end_raw_model_span`) — `nora.app.composition`'s
`_RelevanceGateModelClient` imports these thin wrappers rather than reaching
`strands.telemetry.tracer.get_tracer()` itself, the same pattern
`ask_text`/`ask_structured` already use for a full Agent invocation.

**The vision describer does NOT use this pair, and cannot.**
`nora.infra.vision.client`'s raw call has no model-invoke span of its own at
all — `nora.infra` sits below both `nora.obs` and `nora.agent` in the
layered contract, so it can reach neither this module nor `nora.obs.tracing`
to open one. `nora.ingest.comprehension` (which CAN reach this module, being
above `nora.agent`) still does not wrap the call in a `start_raw_model_span`
either — the describer's model name only reaches the trace after the fact,
via a plain `annotate()` on the outer `comprehend` span, using the model
name `MediaDescription` already carries back on its return value. This is a
known, pre-existing gap (a vision read exports zero token usage), not
something this replacement fixes; widening `MediaDescription` to also carry
usage so a caller could annotate it is the deferred port-shape question
`tests/architecture/test_raw_model_calls_carry_their_own_machinery.py`
records next to the vision client's exemption from its own `usage`
requirement.

This is the strands.telemetry replacement for the hand-rolled
`generation()`/`annotate_generation()` pair `nora.obs.tracing` used to carry —
see the design discussion this replaced in git history (`design/observability/`,
deleted 2026-09-19) for why: Strands' own `Tracer.start_model_invoke_span` /
`end_model_invoke_span` already implement the OTel GenAI semantic
conventions, the redaction lever `design/13-trace-content-and-redaction.md`
investigated, and Langfuse-observation-type wiring for Strands' own version —
upstream-maintained, not a bespoke dialect this repo has to keep two
architecture tests in sync with.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import Sampler
from opentelemetry.trace import Span
from strands.telemetry import StrandsTelemetry
from strands.telemetry.tracer import get_tracer
from strands.types.content import Message, Messages
from strands.types.event_loop import Metrics, StopReason, Usage

log = logging.getLogger("nora.agent.telemetry")

#: OpenAI's `finish_reason` vocabulary, translated to Strands' `StopReason`
#: (a CLOSED `Literal`, unlike OpenAI's plain `str`) so `end_raw_model_span`
#: can hand the SDK's own tracer a value it actually declares. Unmapped values
#: fall through to `"end_turn"` — the same "getting it wrong towards keeping
#: the trace legible costs less than getting it wrong towards dropping it"
#: judgement `nora.obs.tracing`'s sampler makes, one field over: a stop reason
#: is metadata on an already-open span, never a value a caller's control flow
#: depends on.
_STOP_REASON_FROM_FINISH_REASON: dict[str, StopReason] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filtered",
    "tool_calls": "end_turn",
    "function_call": "end_turn",
}


def setup_agent_tracing(
    *, sampler: Sampler, service_name: str, exporter: str, endpoint: str,
    headers_env: str,
) -> None:
    """Register the global `TracerProvider` through `StrandsTelemetry`,
    keeping Nora's own sampler.

    `StrandsTelemetry.__init__` takes an optional pre-built
    `tracer_provider`; passed one, it registers it as-is and skips building
    its own (`strands/telemetry/config.py`) — which is exactly the seam that
    lets Nora's R-76 sampler keep deciding what reaches the collector while
    Strands' own exporter-setup methods do the rest. Passing NO provider
    would build one with no sampler override (OTel's default,
    `ParentBased(root=ALWAYS_ON)`), which would export every ingest-plumbing
    span R-76 exists to drop.
    """
    if exporter not in ("console", "file", "otlp_http"):
        raise ValueError(f"unknown tracing exporter: {exporter!r}")
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=sampler,
    )
    telemetry = StrandsTelemetry(tracer_provider=provider)
    if exporter == "console":
        telemetry.setup_console_exporter()
    elif exporter == "file":
        # The handle must outlive this function — the exporter writes to it
        # for the process's life — so `with open(...)` (which would close it
        # here) is the wrong shape, not the one SIM115 is asking for.
        telemetry.setup_console_exporter(
            out=open(endpoint, "a", encoding="utf-8"),  # noqa: SIM115
        )
    else:
        # `nora.obs.tracing.resolve_otlp_headers` is pure OTel/stdlib (DEC-8's
        # env-var-name-not-value parsing) — importing it here is `nora.agent`
        # reaching DOWN into `nora.obs`, the direction the layered contract
        # allows (see `nora/obs/tracing.py`'s module docstring).
        from ..obs.tracing import resolve_otlp_headers

        telemetry.setup_otlp_exporter(
            endpoint=endpoint, headers=resolve_otlp_headers(headers_env),
        )


def start_raw_model_span(
    *, messages: Messages, model_id: str,
    system_prompt: str | None = None,
    custom_trace_attributes: Mapping[str, Any] | None = None,
) -> Span | None:
    """Open the `chat` span for a raw `chat.completions.create` call, and
    **never load-bearing** — the same rule `nora.obs.tracing.span`/`annotate`
    hold, restated here because this module's raw calls are on the relevance
    gate's hot path: a broken tracer must not turn into a suppressed
    admission decision.

    Currently used by exactly one caller —
    `nora.app.composition._RelevanceGateModelClient`, which stays off the
    Agent SDK because its verdict must not go through `ask_structured`
    (see `nora.agent.structured`'s own docstring for the measured reason).
    `nora.infra.vision.client` is also a raw call but does NOT use this —
    this module's own docstring explains why (`nora.infra` cannot reach it).

    Returns `None` on any failure — including one inside `Tracer.
    start_model_invoke_span` itself, which has no exception guard of its
    own — rather than letting the exception reach the caller's bounded model
    call. `end_raw_model_span`/`end_raw_model_span_with_error` both treat a
    `None` span as "nothing to close" and no-op.
    """
    try:
        return get_tracer().start_model_invoke_span(
            messages=messages, model_id=model_id, system_prompt=system_prompt,
            custom_trace_attributes=custom_trace_attributes,
        )
    except Exception:
        log.debug("raw model span could not start; continuing untraced", exc_info=True)
        return None


def end_raw_model_span(
    *, span: Span | None, content: str, finish_reason: str,
    input_tokens: int | None = None, output_tokens: int | None = None,
    total_tokens: int | None = None, latency_seconds: float | None = None,
) -> None:
    """Close a span opened by `start_raw_model_span`, in the SDK's own
    `Usage`/`Metrics`/`StopReason` vocabulary. **Never load-bearing** — see
    `start_raw_model_span`'s own docstring: a broken tracer, or one that
    never opened (a `None` span, from a guarded `start_raw_model_span`
    failure), must not turn an already-obtained model verdict into a
    discarded one.

    **Usage is written as zero when the provider omitted it**, unlike
    `nora.obs.tracing`'s retired `annotate_generation`, which recorded
    NOTHING rather than assert a free call. `Tracer.end_model_invoke_span`
    writes `gen_ai.usage.*` unconditionally from the `Usage` dict it is
    given — there is no "omit if absent" branch to ask it for. This is a
    real, accepted behavior change from adopting the SDK's own machinery
    rather than the hand-rolled one: OpenAI-compatible providers return usage
    on essentially every response, so the gap this closes is rare, and typing
    it any other way would mean not calling the SDK's own function at all.
    """
    if span is None:
        return
    try:
        stop_reason = _STOP_REASON_FROM_FINISH_REASON.get(finish_reason, "end_turn")
        message: Message = {"role": "assistant", "content": [{"text": content}]}
        usage: Usage = {
            "inputTokens": input_tokens or 0,
            "outputTokens": output_tokens or 0,
            "totalTokens": total_tokens or 0,
        }
        metrics: Metrics = {
            "latencyMs": int((latency_seconds or 0.0) * 1000),
        }
        get_tracer().end_model_invoke_span(
            span=span, message=message, usage=usage, metrics=metrics,
            stop_reason=stop_reason,
        )
    except Exception:
        log.debug("raw model span could not end cleanly", exc_info=True)


def end_raw_model_span_with_error(
    *, span: Span | None, error_message: str, exception: Exception | None = None,
) -> None:
    """The failure half of `start_raw_model_span` — a raw call that raised
    before it returned anything to shape into a `Message`. **Never
    load-bearing**, same as `end_raw_model_span`: the ORIGINAL exception is
    always re-raised by the caller regardless of what happens here, so a
    failure recording the failure must never replace or swallow it."""
    if span is None:
        return
    try:
        get_tracer().end_span_with_error(span, error_message, exception)
    except Exception:
        log.debug("raw model span error not recorded", exc_info=True)


@contextmanager
def timed() -> Iterator[Callable[[], float]]:
    """`with timed() as elapsed: ...` — `elapsed()` returns seconds since
    entry, for `end_raw_model_span`'s `latency_seconds`. A function rather
    than a plain float because the caller reads it AFTER the body runs, and a
    closure is the least ceremony that allows that without a second variable
    at every call site."""
    start = time.monotonic()
    yield lambda: time.monotonic() - start
