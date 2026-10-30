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
(`setup_agent_tracing`) and the span pair a RAW `chat.completions.create` call
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
known gap (a vision read exports zero token usage); widening `MediaDescription`
to also carry usage so a caller could annotate it is the deferred port-shape
question `tests/architecture/test_raw_model_calls_carry_their_own_machinery.py`
records next to the vision client's exemption from its own `usage`
requirement.

Spans are built on Strands' own `Tracer.start_model_invoke_span` /
`end_model_invoke_span`, which already implement the OTel GenAI semantic
conventions, the content-redaction lever, and Langfuse-observation-type wiring:
upstream-maintained rather than a bespoke dialect this repo keeps in sync.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import Sampler
from opentelemetry.trace import Span
from strands.telemetry import StrandsTelemetry
from strands.telemetry.tracer import get_tracer, serialize
from strands.types.content import Message, Messages
from strands.types.event_loop import Metrics, StopReason, Usage

from ..obs.tracing import TraceIdentityProcessor

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
    headers_env: str, credentials: dict[str, str] | None = None,
    service_version: str = "", environment: str = "",
) -> None:
    """Register the global `TracerProvider` through `StrandsTelemetry`,
    keeping Nora's own sampler.

    `StrandsTelemetry.__init__` takes an optional pre-built
    `tracer_provider` — the seam that lets Nora's R-76 sampler keep deciding
    what reaches the collector while Strands' own exporter-setup methods do
    the rest. Passing NO provider would build one with no sampler override
    (OTel's default, `ParentBased(root=ALWAYS_ON)`), which would export every
    ingest-plumbing span R-76 exists to drop.

    ## `set_tracer_provider` IS OURS TO CALL

    Strands does NOT register a supplied provider. From the installed source:

        if tracer_provider:
            self.tracer_provider = tracer_provider   # kept, never registered
        else:
            self._initialize_tracer()                # THIS sets the global

    Its own docstring states the same thing from the other side — "If None, a
    new one will be created *and set as global*" — so the registration is the
    thing you give up by supplying a provider, which is exactly what Nora does
    to keep its sampler.

    Without the explicit `set_tracer_provider` below, the process global stays
    `ProxyTracerProvider`, whose tracers are NO-OPS. Every `nora.obs.tracing.
    span(...)` in the codebase then records nothing and exports nothing —
    **and raises nothing, logs nothing and fails no test**, because a no-op
    tracer is a perfectly well-behaved tracer. The collector can be
    healthy and the try/except around this call silent while nothing is
    exported.
    """
    if exporter not in ("console", "file", "otlp_http"):
        raise ValueError(f"unknown tracing exporter: {exporter!r}")
    resource_attrs: dict[str, str] = {"service.name": service_name}
    # The installed artifact and the deployment, on every span in the process —
    # what "is this new since the last deploy" and "which deployment produced
    # this trace" are answered from. Empty means unknown, never a guessed SHA.
    if service_version:
        resource_attrs["service.version"] = service_version
    if environment:
        resource_attrs["deployment.environment"] = environment
    provider = TracerProvider(
        resource=Resource.create(resource_attrs),
        sampler=sampler,
    )
    # First, so the stamp is on the span before any processor can read it; and
    # on the provider itself, so it reaches the Strands spans and Nora's own
    # alike (tests/unit/test_trace_identity.py reads it through the registered one).
    provider.add_span_processor(TraceIdentityProcessor())
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
            endpoint=endpoint, headers=resolve_otlp_headers(headers_env, credentials),
        )

    # LAST, and unconditional: after whichever exporter was attached above, so
    # the provider that becomes global is one that can actually ship a span.
    # See the docstring — supplying a provider is precisely what stops Strands
    # doing this, and nothing anywhere complains when it is missing.
    trace.set_tracer_provider(provider)


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
    (see `nora.agent.structured`'s own docstring for the reason).
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
    response_model: str = "", provider: str = "",
) -> None:
    """Close a span opened by `start_raw_model_span`, in the SDK's own
    `Usage`/`Metrics`/`StopReason` vocabulary. **Never load-bearing** — see
    `start_raw_model_span`'s own docstring: a broken tracer, or one that
    never opened (a `None` span, from a guarded `start_raw_model_span`
    failure), must not turn an already-obtained model verdict into a
    discarded one.

    **Unknown usage stays unknown — it is never written as zero.**
    `Tracer.end_model_invoke_span` writes `gen_ai.usage.*` unconditionally
    from the `Usage` dict it is given, so passing zeros through it would stamp
    "0 tokens" on a call whose usage the provider simply did not return —
    the fabricated zero the acceptance contract forbids. When the caller has
    no usage at all, the span ends with its message/finish-reason event and
    *no* `gen_ai.usage.*` attributes: absent means "not reported" (R-27), and
    the token/cost totals on the backend treat a missing reading as missing,
    not free. `provider` (the requested lane — OmniRoute or a direct provider)
    and `response_model` (the served model, distinct from the requested alias)
    are attributes on the live span, so they land whichever end path runs.
    """
    if span is None:
        return
    try:
        if provider:
            span.set_attribute("gen_ai.provider.name", provider)
        if response_model and response_model != "":
            span.set_attribute("gen_ai.response.model", response_model)
        stop_reason = _STOP_REASON_FROM_FINISH_REASON.get(finish_reason, "end_turn")
        message: Message = {"role": "assistant", "content": [{"text": content}]}
        if (
            input_tokens is not None
            and output_tokens is not None
            and total_tokens is not None
        ):
            usage: Usage = {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
            }
            metrics: Metrics = {
                "latencyMs": int((latency_seconds or 0.0) * 1000),
            }
            get_tracer().end_model_invoke_span(
                span=span, message=message, usage=usage, metrics=metrics,
                stop_reason=stop_reason,
            )
            return
        # Unknown or PARTIAL usage: end the span with its output event and only
        # the `gen_ai.usage.*` fields that were actually reported. Missing
        # stays missing — `end_model_invoke_span` indexes every field, so it
        # cannot take a partial `Usage`, and filling the gaps with zeros would
        # stamp "0 tokens" on a call whose provider simply did not report that
        # field (the fabricated zero the acceptance contract forbids; R-27).
        # The private helpers are Strands' own span-closing machinery, reached
        # through the same module-level boundary the public call uses — this is
        # the one place a hand-spelled event name is allowed, because the
        # public API has no usage-less end.
        tracer = get_tracer()
        if tracer.use_latest_genai_conventions:
            parts = tracer._map_content_blocks_to_otel_parts(message["content"])
            output = serialize([
                {"role": message["role"], "parts": parts,
                 "finish_reason": str(stop_reason)}
            ])
            tracer._add_event(
                span, "gen_ai.client.inference.operation.details",
                {"gen_ai.output.messages": tracer._redact(
                    "gen_ai.output.messages", output)},
                to_span_attributes=tracer._span_attributes_only,
            )
        else:
            tracer._add_event(
                span, "gen_ai.choice",
                {"finish_reason": str(stop_reason),
                 "message": tracer._redact(
                     "gen_ai.output.messages", serialize(message["content"]))},
            )
        end_attrs: dict[str, Any] = {}
        if input_tokens is not None:
            end_attrs["gen_ai.usage.prompt_tokens"] = input_tokens
            end_attrs["gen_ai.usage.input_tokens"] = input_tokens
        if output_tokens is not None:
            end_attrs["gen_ai.usage.completion_tokens"] = output_tokens
            end_attrs["gen_ai.usage.output_tokens"] = output_tokens
        if total_tokens is not None:
            end_attrs["gen_ai.usage.total_tokens"] = total_tokens
        if latency_seconds is not None and latency_seconds > 0:
            end_attrs["gen_ai.server.request.duration"] = int(latency_seconds * 1000)
        tracer._end_span(span, end_attrs or None)
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


class RawSpanRecorder:
    """The vision client's model-span seam, as an injectable object.

    `nora.infra.vision.client` is below `nora.obs`/`nora.agent` in the layered
    contract, so it cannot reach `start_raw_model_span` itself. The
    composition root hands it one of these instead — an object whose
    `start`/`finish`/`fail` delegate to this module's own functions. The
    client names the protocol it needs (`VisionSpanRecorder`); this is the
    concrete half the boundary allows.
    """

    def start(
        self, *, messages: list, model_id: str, provider: str = "",
    ) -> Span | None:
        # The gen_ai attribute NAMES stay inside this module (the dialect gate
        # in tests/architecture forbids them elsewhere); the provider VALUE is
        # the caller's to declare.
        attrs: dict[str, Any] = {}
        if provider:
            attrs["gen_ai.provider.name"] = provider
        return start_raw_model_span(
            messages=messages, model_id=model_id,
            custom_trace_attributes=attrs or None,
        )

    def finish(
        self, span: Any, *, content: str, finish_reason: str,
        input_tokens: int | None = None, output_tokens: int | None = None,
        total_tokens: int | None = None, latency_seconds: float | None = None,
        response_model: str = "", provider: str = "",
    ) -> None:
        end_raw_model_span(
            span=span, content=content, finish_reason=finish_reason,
            input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=total_tokens, latency_seconds=latency_seconds,
            response_model=response_model, provider=provider,
        )

    def fail(self, span: Any, *, error: Exception) -> None:
        end_raw_model_span_with_error(
            span=span, error_message=type(error).__name__, exception=error,
        )


@contextmanager
def timed() -> Iterator[Callable[[], float]]:
    """`with timed() as elapsed: ...` — `elapsed()` returns seconds since
    entry, for `end_raw_model_span`'s `latency_seconds`. A function rather
    than a plain float because the caller reads it AFTER the body runs, and a
    closure is the least ceremony that allows that without a second variable
    at every call site."""
    start = time.monotonic()
    yield lambda: time.monotonic() - start
